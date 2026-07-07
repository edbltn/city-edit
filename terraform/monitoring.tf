# =============================================================================
# Monitoring MVP — dashboard + uptime check + alert policies
#
# One Cloud Monitoring dashboard ("City Edit — System Health") laid out as a
# live system diagram: a component-health scorecard row (app / OSRM / Redis /
# Cloud SQL) followed by a metrics section per component. Alerts email Eric.
# All of this is pure Cloud Monitoring config — it never touches the running
# services, so it's safe to `terraform apply -target` independently.
# =============================================================================

resource "google_project_service" "monitoring" {
  service = "monitoring.googleapis.com"
}

locals {
  app_filter  = "resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"desire-path-mapper\""
  osrm_filter = "resource.type=\"cloud_run_revision\" resource.label.\"service_name\"=\"desire-path-osrm\""
  redis_filter = "resource.type=\"redis_instance\" resource.label.\"instance_id\"=\"projects/${var.project_id}/locations/${var.region}/instances/${google_redis_instance.cache.name}\""
  sql_filter   = "resource.type=\"cloudsql_database\" resource.label.\"database_id\"=\"${var.project_id}:${google_sql_database_instance.votes.name}\""
}

# -----------------------------------------------------------------------------
# Notification channel
# -----------------------------------------------------------------------------

resource "google_monitoring_notification_channel" "email_eric" {
  display_name = "Eric (email)"
  type         = "email"

  labels = {
    email_address = "eric.didier.bolton@gmail.com"
  }

  depends_on = [google_project_service.monitoring]
}

# -----------------------------------------------------------------------------
# Uptime check — public /health on cityedit.org (200 only when Redis reachable,
# so it covers both the app container and its Redis dependency)
# -----------------------------------------------------------------------------

resource "google_monitoring_uptime_check_config" "app_health" {
  display_name = "City Edit app /health"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = "cityedit.org"
    }
  }

  depends_on = [google_project_service.monitoring]
}

# -----------------------------------------------------------------------------
# Per-endpoint latency — log-based distribution metric
#
# Cloud Run's built-in request_latencies metric has no URL label, so per-
# endpoint percentiles come from the request logs instead: every request log
# entry carries httpRequest.latency + requestUrl. The filter whitelists the
# real API surface — bot scanners probe /api/.env, /api/wp-config.php etc.
# constantly and would otherwise explode the endpoint label's cardinality.
# Values are in SECONDS (extracted from the "0.123s" latency string).
# -----------------------------------------------------------------------------

resource "google_logging_metric" "api_latency" {
  name        = "cityedit_api_latency"
  description = "Per-endpoint request latency (seconds) for the app's real API surface, from Cloud Run request logs"

  filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="desire-path-mapper"
    logName="projects/${var.project_id}/logs/run.googleapis.com%2Frequests"
    httpRequest.requestUrl=~"://[^/]+/(api/(vote|route-votes|routes|geocode|reverse-geocode|nearest-node|graph-votes|graph-topology|graph-version|my-votes|maps|tiles|cities|vote-type-lists)|health)([/?].*)?$"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "s"

    labels {
      key         = "endpoint"
      value_type  = "STRING"
      description = "API path prefix, e.g. /api/vote (subpaths collapse into it)"
    }

    labels {
      key         = "method"
      value_type  = "STRING"
      description = "HTTP method"
    }
  }

  value_extractor = "REGEXP_EXTRACT(httpRequest.latency, \"([0-9]+\\\\.?[0-9]*)s\")"

  label_extractors = {
    endpoint = "REGEXP_EXTRACT(httpRequest.requestUrl, \"://[^/]+(/api/[a-z-]+|/health)\")"
    method   = "EXTRACT(httpRequest.requestMethod)"
  }

  # 1ms … ~30min in ~35% steps — fine-grained where user-facing latency lives.
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 48
      growth_factor      = 1.35
      scale              = 0.001
    }
  }
}

# -----------------------------------------------------------------------------
# Alert policies
# -----------------------------------------------------------------------------

resource "google_monitoring_alert_policy" "app_uptime" {
  display_name = "City Edit: /health failing (app or Redis down)"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check on cityedit.org/health failing"
    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.app_health.uptime_check_id}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 0.5
      duration        = "120s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_FRACTION_TRUE"
        cross_series_reducer = "REDUCE_MEAN"
        group_by_fields      = ["resource.label.host"]
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "cityedit.org/health has been failing for 2+ minutes. It returns 503 when Flask can't reach Redis, and times out when the app service itself is down. Check the Cloud Run service `desire-path-mapper` and Memorystore `desire-path-prod`."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

resource "google_monitoring_alert_policy" "app_5xx_rate" {
  display_name = "City Edit: app 5xx error rate"
  combiner     = "OR"

  conditions {
    display_name = "5xx responses above 0.05/s for 5 min"
    condition_threshold {
      filter          = "${local.app_filter} metric.type=\"run.googleapis.com/request_count\" metric.label.\"response_code_class\"=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.05
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "The app is serving a sustained stream of 5xx (≈15+ errors per 5 min). Usual suspects: gunicorn worker timeout on a graph load, OSRM unreachable, or an OOM-restart loop — check Cloud Run logs for `desire-path-mapper`."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

resource "google_monitoring_alert_policy" "app_p99_latency" {
  display_name = "City Edit: app P99 latency > 5s"
  combiner     = "OR"

  conditions {
    display_name = "P99 request latency above 5s for 5 min"
    condition_threshold {
      filter          = "${local.app_filter} metric.type=\"run.googleapis.com/request_latencies\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5000
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_99"
        cross_series_reducer = "REDUCE_MAX"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "P99 latency on `desire-path-mapper` has exceeded 5s for 5 minutes. Likely head-of-line blocking (gevent hub frozen by a GIL-bound graph load), a cold graph warmup on a fresh instance, or Redis latency."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

# The app runs at ~5.5-6.0Gi of its 8Gi limit in steady state (see the resource
# comment on google_cloud_run_service.app), so alert at 90% — past that a graph
# warmup transient will OOM-kill the instance.
resource "google_monitoring_alert_policy" "app_memory" {
  display_name = "City Edit: app memory > 90% of limit"
  combiner     = "OR"

  conditions {
    display_name = "Flask container memory utilization above 90%"
    condition_threshold {
      filter          = "${local.app_filter} metric.type=\"run.googleapis.com/container/memory/utilizations\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.9
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_99"
        cross_series_reducer = "REDUCE_MAX"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "An app instance is above 90% of its 8Gi memory limit (steady state is ~70-75%). Next stop is an OOM kill mid-request. Check whether a new city graph or an unbounded cache pushed the baseline up."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

resource "google_monitoring_alert_policy" "osrm_memory" {
  display_name = "City Edit: OSRM memory > 90% of limit"
  combiner     = "OR"

  conditions {
    display_name = "OSRM container memory utilization above 90%"
    condition_threshold {
      filter          = "${local.osrm_filter} metric.type=\"run.googleapis.com/container/memory/utilizations\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.9
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_99"
        cross_series_reducer = "REDUCE_MAX"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "OSRM is above 90% of its 4Gi limit. The merged MLD dataset is ~1.6GB resident; growth here means a bigger merged extract landed — bump the limit before it OOMs (it OOM'd at 2Gi once already)."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

# OSRM runs with minScale=maxScale=1; if the instance count falls below 1 the
# routing backend is down even though the public app may still look healthy.
resource "google_monitoring_alert_policy" "osrm_no_instances" {
  display_name = "City Edit: OSRM has no running instance"
  combiner     = "OR"

  conditions {
    display_name = "OSRM instance count below 1 for 5 min"
    condition_threshold {
      filter          = "${local.osrm_filter} metric.type=\"run.googleapis.com/container/instance_count\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }

      # instance_count stops reporting entirely when the service is fully down,
      # so treat a missing series as a violation rather than "no data".
      evaluation_missing_data = "EVALUATION_MISSING_DATA_ACTIVE"
    }
  }

  documentation {
    content   = "The OSRM Cloud Run service (`desire-path-osrm`, pinned min=max=1) has no running instance. Route submission is broken fleet-wide. Check the latest OSRM revision's startup probe / logs."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

resource "google_monitoring_alert_policy" "redis_memory" {
  display_name = "City Edit: Redis memory > 85%"
  combiner     = "OR"

  conditions {
    display_name = "Memorystore memory usage ratio above 85%"
    condition_threshold {
      filter          = "${local.redis_filter} metric.type=\"redis.googleapis.com/stats/memory/usage_ratio\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "Memorystore (`desire-path-prod`, 1GB BASIC) is above 85% memory. Eviction policy is allkeys-lru, so past 100% Redis starts evicting vote/serving keys — the heatmap serves *only* from Redis, so evictions surface as missing votes until a `rebuild_redis_for_map`. Consider bumping `memory_size_gb`."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

resource "google_monitoring_alert_policy" "sql_disk" {
  display_name = "City Edit: Cloud SQL disk > 85%"
  combiner     = "OR"

  conditions {
    display_name = "Postgres disk utilization above 85%"
    condition_threshold {
      filter          = "${local.sql_filter} metric.type=\"cloudsql.googleapis.com/database/disk/utilization\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "Cloud SQL `desire-path-votes-prod` (db-f1-micro) disk is above 85%. Postgres goes read-only when the disk fills — votes stop persisting. Grow the disk or prune old rows."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email_eric.id]
}

# -----------------------------------------------------------------------------
# Dashboard — "City Edit — System Health"
#
# Mosaic grid, 48 columns. Top-to-bottom: architecture text diagram → one
# health scorecard per component → per-component metric sections
# (app → OSRM → Redis → Cloud SQL).
# -----------------------------------------------------------------------------

resource "google_monitoring_dashboard" "system_health" {
  dashboard_json = jsonencode({
    displayName = "City Edit — System Health"
    mosaicLayout = {
      columns = 48
      tiles = [
        # ---- Row 0: architecture diagram ------------------------------------
        {
          width = 48, height = 5
          widget = {
            title = "System"
            text = {
              format = "MARKDOWN"
              content = "**🌐 cityedit.org** → **Cloud Run `desire-path-mapper`** (nginx + Flask, 8Gi × 1–4 instances) → **OSRM** `desire-path-osrm` (routing, 4Gi × 1) · **Redis** Memorystore `desire-path-prod` (serving votes, 1GB LRU) · **Cloud SQL** `desire-path-votes-prod` (canonical votes)\n\nScorecards below = component health at a glance. Sections: App → API latency by endpoint (grouped by backing store) → OSRM → Redis → SQL."
              # The API echoes an empty style object back; without it every plan
              # shows a perpetual dashboard_json diff.
              style = {}
            }
          }
        },

        # ---- Row 1: component health scorecards -----------------------------
        {
          yPos = 5, width = 8, height = 8
          widget = {
            title = "App uptime (/health)"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" resource.type=\"uptime_url\" metric.label.\"check_id\"=\"${google_monitoring_uptime_check_config.app_health.uptime_check_id}\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_FRACTION_TRUE"
                    crossSeriesReducer = "REDUCE_MEAN"
                  }
                }
              }
              gaugeView  = { upperBound = 1 }
              thresholds = [{ value = 0.95, color = "RED", direction = "BELOW" }]
            }
          }
        },
        {
          xPos = 8, yPos = 5, width = 8, height = 8
          widget = {
            title = "App P99 latency (ms)"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "${local.app_filter} metric.type=\"run.googleapis.com/request_latencies\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_PERCENTILE_99"
                    crossSeriesReducer = "REDUCE_MAX"
                  }
                }
              }
              sparkChartView = { sparkChartType = "SPARK_LINE" }
              thresholds = [
                { value = 2000, color = "YELLOW", direction = "ABOVE" },
                { value = 5000, color = "RED", direction = "ABOVE" },
              ]
            }
          }
        },
        {
          xPos = 16, yPos = 5, width = 8, height = 8
          widget = {
            title = "App memory (of 8Gi)"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "${local.app_filter} metric.type=\"run.googleapis.com/container/memory/utilizations\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_PERCENTILE_99"
                    crossSeriesReducer = "REDUCE_MAX"
                  }
                }
              }
              gaugeView = { upperBound = 1 }
              thresholds = [
                { value = 0.8, color = "YELLOW", direction = "ABOVE" },
                { value = 0.9, color = "RED", direction = "ABOVE" },
              ]
            }
          }
        },
        {
          xPos = 24, yPos = 5, width = 8, height = 8
          widget = {
            title = "OSRM memory (of 4Gi)"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "${local.osrm_filter} metric.type=\"run.googleapis.com/container/memory/utilizations\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_PERCENTILE_99"
                    crossSeriesReducer = "REDUCE_MAX"
                  }
                }
              }
              gaugeView = { upperBound = 1 }
              thresholds = [
                { value = 0.8, color = "YELLOW", direction = "ABOVE" },
                { value = 0.9, color = "RED", direction = "ABOVE" },
              ]
            }
          }
        },
        {
          xPos = 32, yPos = 5, width = 8, height = 8
          widget = {
            title = "Redis memory (of 1GB)"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "${local.redis_filter} metric.type=\"redis.googleapis.com/stats/memory/usage_ratio\""
                  aggregation = {
                    alignmentPeriod  = "300s"
                    perSeriesAligner = "ALIGN_MEAN"
                  }
                }
              }
              gaugeView = { upperBound = 1 }
              thresholds = [
                { value = 0.75, color = "YELLOW", direction = "ABOVE" },
                { value = 0.85, color = "RED", direction = "ABOVE" },
              ]
            }
          }
        },
        {
          xPos = 40, yPos = 5, width = 8, height = 8
          widget = {
            title = "SQL CPU"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "${local.sql_filter} metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\""
                  aggregation = {
                    alignmentPeriod  = "300s"
                    perSeriesAligner = "ALIGN_MEAN"
                  }
                }
              }
              gaugeView = { upperBound = 1 }
              thresholds = [
                { value = 0.7, color = "YELLOW", direction = "ABOVE" },
                { value = 0.9, color = "RED", direction = "ABOVE" },
              ]
            }
          }
        },

        # ---- App section -----------------------------------------------------
        {
          yPos = 13, width = 16, height = 10
          widget = {
            title = "App — requests/s by response class"
            xyChart = {
              dataSets = [{
                plotType = "STACKED_AREA"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.app_filter} metric.type=\"run.googleapis.com/request_count\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                      groupByFields      = ["metric.label.\"response_code_class\""]
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 16, yPos = 13, width = 16, height = 10
          widget = {
            title = "App — request latency P50 / P95 / P99 (ms)"
            xyChart = {
              dataSets = [
                {
                  plotType = "LINE"
                  legendTemplate = "P50"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.app_filter} metric.type=\"run.googleapis.com/request_latencies\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_PERCENTILE_50"
                        crossSeriesReducer = "REDUCE_MAX"
                      }
                    }
                  }
                },
                {
                  plotType = "LINE"
                  legendTemplate = "P95"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.app_filter} metric.type=\"run.googleapis.com/request_latencies\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_PERCENTILE_95"
                        crossSeriesReducer = "REDUCE_MAX"
                      }
                    }
                  }
                },
                {
                  plotType = "LINE"
                  legendTemplate = "P99"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.app_filter} metric.type=\"run.googleapis.com/request_latencies\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_PERCENTILE_99"
                        crossSeriesReducer = "REDUCE_MAX"
                      }
                    }
                  }
                },
              ]
            }
          }
        },
        {
          xPos = 32, yPos = 13, width = 16, height = 10
          widget = {
            title = "App — memory utilization (P99 per instance)"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.app_filter} metric.type=\"run.googleapis.com/container/memory/utilizations\""
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_PERCENTILE_99"
                      crossSeriesReducer = "REDUCE_MAX"
                      groupByFields      = ["resource.label.\"revision_name\""]
                    }
                  }
                }
              }]
              thresholds = [{ value = 0.9 }]
            }
          }
        },
        {
          yPos = 23, width = 16, height = 10
          widget = {
            title = "App — CPU utilization (P99 per instance)"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.app_filter} metric.type=\"run.googleapis.com/container/cpu/utilizations\""
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_PERCENTILE_99"
                      crossSeriesReducer = "REDUCE_MAX"
                      groupByFields      = ["resource.label.\"revision_name\""]
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 16, yPos = 23, width = 16, height = 10
          widget = {
            title = "App — instance count (autoscaler, max 4)"
            xyChart = {
              dataSets = [{
                plotType = "STACKED_AREA"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.app_filter} metric.type=\"run.googleapis.com/container/instance_count\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_MAX"
                      crossSeriesReducer = "REDUCE_SUM"
                      groupByFields      = ["metric.label.\"state\""]
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 32, yPos = 23, width = 16, height = 10
          widget = {
            title = "App — concurrent requests (P99)"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.app_filter} metric.type=\"run.googleapis.com/container/max_request_concurrencies\""
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_PERCENTILE_99"
                      crossSeriesReducer = "REDUCE_MAX"
                    }
                  }
                }
              }]
            }
          }
        },

        # ---- API latency by endpoint (log-based metric, seconds) -------------
        # Grouped by what each endpoint leans on, so a slow group points at its
        # backing store. Data starts at metric creation (log-based metrics
        # aren't retroactive).
        {
          yPos = 33, width = 24, height = 10
          widget = {
            title = "API P99 by endpoint — Redis / Postgres backed (s)"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.api_latency.name}\" resource.type=\"cloud_run_revision\" metric.label.\"endpoint\"=monitoring.regex.full_match(\"/api/(vote|graph-votes|my-votes|route-votes|maps)|/health\")"
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_99"
                      groupByFields      = ["metric.label.\"endpoint\""]
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 24, yPos = 33, width = 24, height = 10
          widget = {
            title = "/api/routes (OSRM) — P50 / P95 / P99 (s)"
            xyChart = {
              dataSets = [
                {
                  plotType = "LINE"
                  legendTemplate = "P50"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.api_latency.name}\" resource.type=\"cloud_run_revision\" metric.label.\"endpoint\"=\"/api/routes\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_DELTA"
                        crossSeriesReducer = "REDUCE_PERCENTILE_50"
                      }
                    }
                  }
                },
                {
                  plotType = "LINE"
                  legendTemplate = "P95"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.api_latency.name}\" resource.type=\"cloud_run_revision\" metric.label.\"endpoint\"=\"/api/routes\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_DELTA"
                        crossSeriesReducer = "REDUCE_PERCENTILE_95"
                      }
                    }
                  }
                },
                {
                  plotType = "LINE"
                  legendTemplate = "P99"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.api_latency.name}\" resource.type=\"cloud_run_revision\" metric.label.\"endpoint\"=\"/api/routes\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_DELTA"
                        crossSeriesReducer = "REDUCE_PERCENTILE_99"
                      }
                    }
                  }
                },
              ]
            }
          }
        },
        {
          yPos = 43, width = 24, height = 10
          widget = {
            title = "API P99 by endpoint — in-process graph provider (s)"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.api_latency.name}\" resource.type=\"cloud_run_revision\" metric.label.\"endpoint\"=monitoring.regex.full_match(\"/api/(reverse-geocode|nearest-node|graph-topology|graph-version)\")"
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_99"
                      groupByFields      = ["metric.label.\"endpoint\""]
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 24, yPos = 43, width = 24, height = 10
          widget = {
            title = "API P99 by endpoint — external (Photon) + static (s)"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.api_latency.name}\" resource.type=\"cloud_run_revision\" metric.label.\"endpoint\"=monitoring.regex.full_match(\"/api/(geocode|tiles|cities|vote-type-lists)\")"
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_99"
                      groupByFields      = ["metric.label.\"endpoint\""]
                    }
                  }
                }
              }]
            }
          }
        },

        # ---- OSRM section ----------------------------------------------------
        {
          yPos = 53, width = 16, height = 10
          widget = {
            title = "OSRM — requests/s by response class"
            xyChart = {
              dataSets = [{
                plotType = "STACKED_AREA"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.osrm_filter} metric.type=\"run.googleapis.com/request_count\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                      groupByFields      = ["metric.label.\"response_code_class\""]
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 16, yPos = 53, width = 16, height = 10
          widget = {
            title = "OSRM — latency P50 / P99 (ms)"
            xyChart = {
              dataSets = [
                {
                  plotType = "LINE"
                  legendTemplate = "P50"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.osrm_filter} metric.type=\"run.googleapis.com/request_latencies\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_PERCENTILE_50"
                        crossSeriesReducer = "REDUCE_MAX"
                      }
                    }
                  }
                },
                {
                  plotType = "LINE"
                  legendTemplate = "P99"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.osrm_filter} metric.type=\"run.googleapis.com/request_latencies\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_PERCENTILE_99"
                        crossSeriesReducer = "REDUCE_MAX"
                      }
                    }
                  }
                },
              ]
            }
          }
        },
        {
          xPos = 32, yPos = 53, width = 16, height = 10
          widget = {
            title = "OSRM — memory & CPU (P99)"
            xyChart = {
              dataSets = [
                {
                  plotType = "LINE"
                  legendTemplate = "memory"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.osrm_filter} metric.type=\"run.googleapis.com/container/memory/utilizations\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_PERCENTILE_99"
                        crossSeriesReducer = "REDUCE_MAX"
                      }
                    }
                  }
                },
                {
                  plotType = "LINE"
                  legendTemplate = "cpu"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.osrm_filter} metric.type=\"run.googleapis.com/container/cpu/utilizations\""
                      aggregation = {
                        alignmentPeriod    = "300s"
                        perSeriesAligner   = "ALIGN_PERCENTILE_99"
                        crossSeriesReducer = "REDUCE_MAX"
                      }
                    }
                  }
                },
              ]
            }
          }
        },

        # ---- Redis section ---------------------------------------------------
        {
          yPos = 63, width = 16, height = 10
          widget = {
            title = "Redis — memory usage ratio"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.redis_filter} metric.type=\"redis.googleapis.com/stats/memory/usage_ratio\""
                    aggregation = {
                      alignmentPeriod  = "60s"
                      perSeriesAligner = "ALIGN_MEAN"
                    }
                  }
                }
              }]
              thresholds = [{ value = 0.85 }]
            }
          }
        },
        {
          xPos = 16, yPos = 63, width = 16, height = 10
          widget = {
            title = "Redis — connected clients"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.redis_filter} metric.type=\"redis.googleapis.com/clients/connected\""
                    aggregation = {
                      alignmentPeriod  = "60s"
                      perSeriesAligner = "ALIGN_MEAN"
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 32, yPos = 63, width = 16, height = 10
          widget = {
            title = "Redis — commands/s"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.redis_filter} metric.type=\"redis.googleapis.com/commands/calls\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                    }
                  }
                }
              }]
            }
          }
        },

        # ---- Cloud SQL section -----------------------------------------------
        {
          yPos = 73, width = 16, height = 10
          widget = {
            title = "Cloud SQL — CPU & memory utilization"
            xyChart = {
              dataSets = [
                {
                  plotType = "LINE"
                  legendTemplate = "cpu"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.sql_filter} metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\""
                      aggregation = {
                        alignmentPeriod  = "300s"
                        perSeriesAligner = "ALIGN_MEAN"
                      }
                    }
                  }
                },
                {
                  plotType = "LINE"
                  legendTemplate = "memory"
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "${local.sql_filter} metric.type=\"cloudsql.googleapis.com/database/memory/utilization\""
                      aggregation = {
                        alignmentPeriod  = "300s"
                        perSeriesAligner = "ALIGN_MEAN"
                      }
                    }
                  }
                },
              ]
            }
          }
        },
        {
          xPos = 16, yPos = 73, width = 16, height = 10
          widget = {
            title = "Cloud SQL — active connections"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.sql_filter} metric.type=\"cloudsql.googleapis.com/database/postgresql/num_backends\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_MEAN"
                      crossSeriesReducer = "REDUCE_SUM"
                    }
                  }
                }
              }]
            }
          }
        },
        {
          xPos = 32, yPos = 73, width = 16, height = 10
          widget = {
            title = "Cloud SQL — disk utilization"
            xyChart = {
              dataSets = [{
                plotType = "LINE"
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "${local.sql_filter} metric.type=\"cloudsql.googleapis.com/database/disk/utilization\""
                    aggregation = {
                      alignmentPeriod  = "300s"
                      perSeriesAligner = "ALIGN_MEAN"
                    }
                  }
                }
              }]
              thresholds = [{ value = 0.85 }]
            }
          }
        },
      ]
    }
  })

  depends_on = [google_project_service.monitoring]
}

output "dashboard_url" {
  description = "Cloud Monitoring system-health dashboard"
  value       = "https://console.cloud.google.com/monitoring/dashboards/builder/${element(split("/", google_monitoring_dashboard.system_health.id), length(split("/", google_monitoring_dashboard.system_health.id)) - 1)}?project=${var.project_id}"
}
