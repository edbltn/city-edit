// ==========================================================================
// Vote-type selector — the map's legend AND the cast-target picker
// ==========================================================================
// One list, two jobs, because they are the same list: every row is a vote type
// whose icon is painted on the map (as a top-proposal pin) and whose support is
// painted into the heat. So the panel reads as a legend — icon, name, net
// support — and each row carries the two things you can do with a legend entry:
//
//   the eye  → show/hide this type's heat and pins  (map/voteTypeFilter)
//   the row  → make it the type you're voting for   (RouteContext.voteType)
//
// The panel is split into the map's two proposal FAMILIES — Point proposals
// (square pins) and Route proposals (diamond corridors) — each with its own
// heading and its own show-all/hide-all eye. Grouping by family rather than by
// "castable right now" keeps the headings stable: a corridor type doesn't move
// house the moment you finish drawing a route. The family you can currently
// cast into leads, and the other carries a note saying how to reach it.
// ==========================================================================

import { useState, useCallback, useEffect, useLayoutEffect, useRef, useMemo, memo } from "react";
import { useRoute, useTheme, useMap } from "../../context";
import { getSuggestionsForTheme } from "../../constants/voteTypes";
import {
  toggleVoteTypeVisible,
  setVoteTypesVisible,
  showOnlyVoteTypes,
} from "../../map/voteTypeFilter";
import { registerVoteTypeLabel } from "../../map/voteTypeRegistry";
import { useHiddenVoteTypes, useVoteTypeLegend } from "../../hooks/useVoteTypeLegend";
import { iconSrc, iconForLabel } from "../../themes";
import { suggestionGlyphForLabel } from "../../utils/suggestionIcon";
import { EyeIcon } from "../EyeIcon";
import { noAutofillProps } from "../../utils/noAutofill";
import "./VoteTypeSelector.css";

/** One legend row: a vote type on this map. */
interface PanelRow {
  label: string;
  /** Themed icon path, or null → the colorized suggestion glyph. */
  icon: string | null;
  /** Signed net support across the map (0 = no votes / perfectly contested). */
  net: number;
  /** Selectable as the cast target in the current selection mode. */
  castable: boolean;
  /** Which proposal family the type belongs to — decides its section. */
  family: "route" | "point";
}

/** A headed group of rows: one proposal family, with its own show/hide-all. */
interface PanelSection {
  key: string;
  title: string;
  rows: PanelRow[];
}

/** Support count for a legend row — "1,204", "−412", "" at zero. */
function formatNet(net: number): string {
  if (!net) return "";
  const abs = Math.abs(net).toLocaleString("en-US");
  return net < 0 ? `−${abs}` : abs;
}

export const VoteTypeSelector = memo(function VoteTypeSelector() {
  const { voteType, requestedVoteType, setVoteType, pointType } = useRoute();
  const theme = useTheme();
  const map = useMap();
  const hidden = useHiddenVoteTypes();
  const legend = useVoteTypeLegend(pointType);
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  // Upper bound for the (left-anchored) dropdown so it grows to the viewport's
  // right edge then ellipsizes, never overflowing. Measured from the box's left.
  const [dropdownMaxWidth, setDropdownMaxWidth] = useState<number>();
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputValueRef = useRef("");
  // The REQUESTED vote type when the field opened, restored if editing is
  // cancelled. Deliberately the raw request (often "") rather than the displayed
  // effective label: restoring the resolved label would commit a choice the user
  // never made and stamp `vt=` onto the URL just for opening and closing the box.
  const previousVoteTypeRef = useRef(requestedVoteType);

  const isStationNetwork = (map?.network ?? "streets") !== "streets";

  // The legend proper: every vote type the map draws, plus its authored types.
  // A map that authored none of its own (legacy/preset maps) falls back to the
  // theme's preset suggestions so there is still something to cast.
  const rows: PanelRow[] = useMemo(() => {
    // A legacy label the DB never flagged has no family of its own — its votes
    // surface in BOTH, so file it under the one you can cast into right now.
    const familyOf = (kind: "route" | "point" | null): "route" | "point" =>
      kind ?? pointType;
    const base: PanelRow[] = legend.map((e) => ({
      label: e.label,
      icon: e.icon,
      net: e.net,
      castable: e.castable,
      family: familyOf(e.pointType),
    }));
    if (map?.voteTypes?.length) return base;
    const known = new Set(base.map((r) => r.label));
    const presets = getSuggestionsForTheme(theme, pointType)
      .filter((s) => !known.has(s.label))
      .map((s): PanelRow => ({
        label: s.label, icon: s.icon, net: 0, castable: true,
        family: familyOf(s.pointType ?? null),
      }));
    return [...presets, ...base];
  }, [legend, map, theme, pointType]);

  const inputLower = inputValue.trim().toLowerCase();
  const matchingRows = inputLower
    ? rows.filter((r) => r.label.toLowerCase().includes(inputLower))
    : rows;

  // Custom vote types the server reported at load but that carry no live net
  // (so buildVoteTypeLegend left them out of the legend). They surface only
  // while typing — never in the default list — and are kind-filtered like the
  // rest; station networks skip that filter (they only ever vote points,
  // whatever kind the type was authored as).
  const knownLabels = useMemo(() => new Set(rows.map((r) => r.label)), [rows]);
  const searchOnlyRows: PanelRow[] = inputLower
    ? (map?.searchVoteTypes ?? [])
        .filter((vt) => !knownLabels.has(vt.label) && vt.label.toLowerCase().includes(inputLower))
        .map((vt): PanelRow => ({
          label: vt.label,
          icon: iconForLabel(vt.label, map?.voteTypes),
          net: 0,
          castable: isStationNetwork || !vt.pointType || vt.pointType === pointType,
          family: vt.pointType ?? pointType,
        }))
    : [];

  const listRows = [...matchingRows, ...searchOnlyRows];

  const hasExactMatch = listRows.some((r) => r.label.toLowerCase() === inputLower);
  // Maps that disallow user suggestions can't add custom vote types.
  const allowCustom = map ? map.allowSuggestions : true;
  const showCustomOption = allowCustom && inputLower !== "" && !hasExactMatch;

  // When suggestions are off and there's a single choice, there's nothing to
  // pick — show a frozen chip, not a dropdown. "Single choice" covers two cases:
  //   1. the map authored exactly one vote type overall, or
  //   2. the pointType-filtered list happens to have one entry.
  // Case 1 must ignore pointType: a route-only single type is filtered out of
  // the castable rows while in point mode (before both endpoints are set), which
  // previously left the field unlocked until a full route was drawn. Keying the
  // lock off the map's full list keeps it frozen in every mode.
  const mapVoteTypes = map?.voteTypes ?? [];
  const castableRows = rows.filter((r) => r.castable);
  const frozenLabel = !allowCustom
    ? mapVoteTypes.length === 1
      ? mapVoteTypes[0].label
      : castableRows.length === 1
        ? castableRows[0].label
        : null
    : null;

  // One section per proposal FAMILY (docs/three-layer-model.md §3.1): point
  // proposals are square pins, route proposals are diamond corridors. The family
  // you can cast into leads; the other keeps its heading and its eye, and says
  // how to reach it. Station networks vote on fixed points whatever kind their
  // types were authored as, so they get one undivided list.
  const sections: PanelSection[] = isStationNetwork
    ? (listRows.length ? [{ key: "all", title: "Proposals", rows: listRows }] : [])
    : (pointType === "route" ? (["route", "point"] as const) : (["point", "route"] as const))
        .map((family): PanelSection => ({
          key: family,
          title: family === "route" ? "Route proposals" : "Point proposals",
          rows: listRows.filter((r) => r.family === family),
        }))
        .filter((s) => s.rows.length > 0);

  // Rendered order — what the arrow keys walk and what highlightedIndex means.
  const orderedRows = sections.flatMap((s) => s.rows);

  // Is anything on THIS map hidden? Measured over the whole legend, not the
  // search-filtered view, so it's a statement about the map and not the query.
  const isFiltered = rows.some((r) => hidden.has(r.label));

  // Keyboard navigation runs over the rows plus the trailing "Suggest:" option.
  const optionCount = orderedRows.length + (showCustomOption ? 1 : 0);

  useEffect(() => {
    setHighlightedIndex(0);
  }, [inputValue]);

  // Cap the dropdown at the viewport's right edge: its CSS width is `max-content`
  // (grows with the longest label), so without a cap a long custom suggestion
  // could run off-screen. Measure once on open and on resize — the box itself is
  // fixed width, so its left edge doesn't move while the dropdown is open.
  useLayoutEffect(() => {
    if (!isOpen) return;
    const update = () => {
      const el = containerRef.current;
      if (!el) return;
      const { left } = el.getBoundingClientRect();
      setDropdownMaxWidth(Math.max(240, Math.round(window.innerWidth - left - 8)));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [isOpen]);

  const selectOption = useCallback(
    (label: string, isCustom = false) => {
      // Committing a brand-new suggestion registers it immediately, so it joins
      // the legend and the searchable list before the cast round-trips.
      if (isCustom) registerVoteTypeLabel(label);
      setVoteType(label);
      setInputValue("");
      inputValueRef.current = "";
      setIsOpen(false);
      inputRef.current?.blur();
    },
    [setVoteType]
  );

  const handleFocus = useCallback(() => {
    // Remember the requested type so Escape / empty-close can revert to it.
    previousVoteTypeRef.current = requestedVoteType;
    setIsOpen(true);
    setInputValue("");
    inputValueRef.current = "";
    setHighlightedIndex(0);
  }, [requestedVoteType]);

  const handleBlur = useCallback(() => {}, []);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const next = e.target.value;
    setInputValue(next);
    inputValueRef.current = next;
    setIsOpen(true);
    // Typing makes the typed text the live vote target, so casting re-enables
    // immediately. A real commit still only happens on explicit selection.
    setVoteType(next);
  }, [setVoteType]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlightedIndex((prev) => Math.min(prev + 1, optionCount - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlightedIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (optionCount > 0) {
          const row = orderedRows[highlightedIndex];
          if (row) {
            if (row.castable) selectOption(row.label);
          } else if (showCustomOption) {
            selectOption(inputValue.trim(), true);
          }
        } else if (inputValue.trim()) {
          selectOption(inputValue.trim(), true);
        }
      } else if (e.key === "Escape") {
        setVoteType(previousVoteTypeRef.current);
        setIsOpen(false);
        setInputValue("");
        inputValueRef.current = "";
        inputRef.current?.blur();
      }
    },
    [optionCount, orderedRows, highlightedIndex, inputValue, showCustomOption, selectOption, setVoteType]
  );

  const handleChevronClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (isOpen) {
      const trimmed = inputValueRef.current.trim();
      if (trimmed) {
        selectOption(trimmed, true);
      } else {
        setVoteType(previousVoteTypeRef.current);
        setIsOpen(false);
        inputRef.current?.blur();
      }
    } else {
      inputRef.current?.focus();
    }
  }, [isOpen, selectOption, setVoteType]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        const trimmed = inputValueRef.current.trim();
        if (trimmed) {
          selectOption(trimmed, true);
        } else if (isOpen) {
          // Cancelled with an empty field — revert the live-typed vote type.
          setVoteType(previousVoteTypeRef.current);
          setIsOpen(false);
        }
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [selectOption, setVoteType, isOpen]);

  // Visibility gestures fire on mousedown and swallow the event: the row's own
  // mousedown would otherwise commit the type and close the panel, and the
  // default would pull focus off the search field mid-toggle.
  const swallow = (e: React.MouseEvent, run: () => void) => {
    e.preventDefault();
    e.stopPropagation();
    run();
  };

  // The committed type's themed icon: the map's own vote-type icon wins (user
  // maps define their own), then a matching preset icon. Null → it's a custom
  // type and renders the colorized suggestion glyph instead.
  const displayIcon = voteType ? iconForLabel(voteType, map?.voteTypes) : null;

  // A single, fixed vote type renders a static chip (no input/chevron/dropdown);
  // the vote type defaults to it via getDefaultVoteTypeForTheme, so casting works.
  const frozenIcon = frozenLabel ? iconForLabel(frozenLabel, map?.voteTypes) : null;

  /**
   * A heading row: a show-all/hide-all eye, a title, and how much of the group
   * is currently drawn. Used for each proposal family and for the "All
   * proposals" master row, so the two are the same control at two scales.
   *
   * The eye sits in the same left column as the rows' own eyes, so a section
   * and its members read as one unit. From a MIXED group it shows everything —
   * the recovering direction, since a blank map is the outcome worth making
   * people ask for twice.
   */
  const groupHeader = (title: string, groupRows: PanelRow[], extraClass = "") => {
    const shown = groupRows.reduce((n, r) => n + (hidden.has(r.label) ? 0 : 1), 0);
    const allVisible = shown === groupRows.length;
    return (
      <div className={`vt-section ${extraClass}`.trim()}>
        <button
          type="button"
          className={`vt-vis vt-vis-all${allVisible ? "" : " is-off"}`}
          role="switch"
          aria-checked={allVisible}
          aria-label={`${allVisible ? "Hide" : "Show"} all ${title.toLowerCase()}`}
          title={`${allVisible ? "Hide" : "Show"} all ${title.toLowerCase()}`}
          onMouseDown={(e) => swallow(e, () =>
            setVoteTypesVisible(groupRows.map((r) => r.label), !allVisible)
          )}
        >
          <EyeIcon size={14} off={shown === 0} />
        </button>
        <span className="vt-section-title">{title}</span>
        <span className={`vt-section-count${allVisible ? "" : " is-filtered"}`}>
          ({shown}/{groupRows.length} shown)
        </span>
      </div>
    );
  };

  const renderIcon = (icon: string | null, label: string) =>
    icon ? (
      <img className="vote-type-icon-img" src={iconSrc(icon)} alt="" />
    ) : (
      <span
        className="vote-type-icon-img"
        dangerouslySetInnerHTML={{ __html: suggestionGlyphForLabel(label, 18) }}
      />
    );

  return (
    <div
      ref={containerRef}
      className={`vote-type-selector ${frozenLabel ? "vote-type-frozen" : isOpen ? "active" : ""}`}
      title={frozenLabel ?? undefined}
    >
      {frozenLabel ? (
        <div className="vote-type-control">
          {renderIcon(frozenIcon, frozenLabel)}
          <span className="vote-type-display-text">{frozenLabel}</span>
        </div>
      ) : (
      <>
      <div className="vote-type-control">
        <input
          ref={inputRef}
          type="text"
          name="vote-type"
          className="vote-type-input"
          value={isOpen ? inputValue : ""}
          onChange={handleInputChange}
          onFocus={handleFocus}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          placeholder={isOpen ? "Type to search..." : ""}
          spellCheck={false}
          autoCorrect="off"
          autoCapitalize="off"
          role="combobox"
          aria-expanded={isOpen}
          aria-autocomplete="list"
          {...noAutofillProps}
        />
        {!isOpen && voteType && (
          <div className="vote-type-display">
            {renderIcon(displayIcon, voteType)}
            <span className="vote-type-display-text">{voteType}</span>
          </div>
        )}
        {!isOpen && !voteType && (
          <div className="vote-type-placeholder">
            {allowCustom ? "Suggest a change..." : "Select a vote type"}
          </div>
        )}
        {/* A filtered map is easy to mistake for an empty one, so the collapsed
            control still says so — the struck-through eye, no count. */}
        {!isOpen && isFiltered && (
          <span
            className="vote-type-filter-badge"
            title="Some proposal types are hidden on the map"
          >
            <EyeIcon size={13} off />
          </span>
        )}
        <span className="vote-type-chevron" onMouseDown={handleChevronClick}>
          <span className="caret-down" />
        </span>
      </div>

      {isOpen && (
        <div
          className="vote-type-dropdown"
          role="listbox"
          style={dropdownMaxWidth ? { maxWidth: dropdownMaxWidth } : undefined}
        >
          {/* Master row. Only earns its place when there's more than one family
              to reach across — with a single section it would just shadow that
              section's own eye. */}
          {sections.length > 1 && groupHeader("All proposals", orderedRows, "vt-section-all")}

          {sections.map((section) => (
              <div key={section.key} className="vt-group">
                {groupHeader(section.title, section.rows)}

                {section.rows.map((row) => {
                  const index = orderedRows.indexOf(row);
                  const isHighlighted = index === highlightedIndex;
                  const isCasting = row.label === voteType;
                  const visible = !hidden.has(row.label);
                  const cls = [
                    "vote-type-option",
                    isHighlighted ? "highlighted" : "",
                    isCasting ? "is-casting" : "",
                    visible ? "" : "is-hidden",
                    row.castable ? "" : "is-uncastable",
                  ].filter(Boolean).join(" ");

                  return (
                    <div
                      key={row.label}
                      className={cls}
                      role="option"
                      aria-selected={isCasting}
                      onMouseDown={() => row.castable && selectOption(row.label)}
                      onMouseEnter={() => setHighlightedIndex(index)}
                      title={row.castable ? undefined
                        : `Drawn on the map. Draw ${pointType === "point" ? "a route" : "a single point"} to vote for it.`}
                    >
                      <button
                        type="button"
                        className={`vt-vis${visible ? "" : " is-off"}`}
                        role="switch"
                        aria-checked={visible}
                        aria-label={`${visible ? "Hide" : "Show"} ${row.label} on the map`}
                        title={`${visible ? "Hide" : "Show"} on the map`}
                        onMouseDown={(e) => swallow(e, () => toggleVoteTypeVisible(row.label))}
                      >
                        <EyeIcon size={14} off={!visible} />
                      </button>
                      {renderIcon(row.icon, row.label)}
                      <span className="vote-type-label">{row.label}</span>
                      <span className="vt-tail">
                        <span className={`vt-net${row.net < 0 ? " is-negative" : ""}`}>
                          {formatNet(row.net)}
                        </span>
                        <button
                          type="button"
                          className="vt-only"
                          title={`Show only ${row.label}`}
                          onMouseDown={(e) => swallow(e, () =>
                            showOnlyVoteTypes(rows.map((r) => r.label), [row.label])
                          )}
                        >
                          only
                        </button>
                      </span>
                    </div>
                  );
                })}
              </div>
          ))}

          {showCustomOption && (
            <div
              className={`vote-type-option vote-type-custom ${
                highlightedIndex === orderedRows.length ? "highlighted" : ""}`}
              onMouseDown={() => selectOption(inputValue.trim(), true)}
              onMouseEnter={() => setHighlightedIndex(orderedRows.length)}
            >
              <span>Suggest: <em>"{inputValue.trim()}"</em></span>
            </div>
          )}

          {optionCount === 0 && (
            <div className="vt-empty">No proposal types match “{inputValue.trim()}”</div>
          )}
        </div>
      )}
      </>
      )}
    </div>
  );
});
