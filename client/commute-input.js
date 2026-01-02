const GEOCODE_API_URL = "http://localhost:5001/api/geocode";
const DEBOUNCE_MS = 300;

export function createCommuteInputModal(map) {
  const state = {
    origin: null,
    destination: null,
    selectedMode: "bike",  // Options: 'bike', 'walk', 'drive'
    markers: {
      origin: null,
      destination: null
    }
  };

  let debounceTimer = null;

  function debounce(fn, delay) {
    return (...args) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => fn(...args), delay);
    };
  }

  async function fetchGeocodeResults(query) {
    if (!query || query.length < 3) return [];

    try {
      const response = await fetch(`${GEOCODE_API_URL}?q=${encodeURIComponent(query)}`);
      if (!response.ok) throw new Error("Geocode request failed");
      return await response.json();
    } catch {
      return [];
    }
  }

  function renderAutocompleteResults(results, container) {
    container.innerHTML = "";

    if (results.length === 0) {
      container.classList.remove("active");
      return;
    }

    container.classList.add("active");

    results.forEach(result => {
      const item = document.createElement("div");
      item.className = "autocomplete-item";
      item.textContent = result.display_name;
      item.dataset.lat = result.lat;
      item.dataset.lon = result.lon;

      item.addEventListener("click", () => {
        selectLocation(result, container);
      });

      container.appendChild(item);
    });
  }

  function selectLocation(result, container) {
    const inputField = container.previousElementSibling;
    inputField.value = result.display_name;
    container.classList.remove("active");
    container.innerHTML = "";

    // Store location based on which field was used
    const type = inputField.id === "origin-input" ? "origin" : "destination";
    state[type] = { lat: result.lat, lon: result.lon, name: result.display_name };

    // Update marker
    updateMarker(type, result.lat, result.lon);
  }

  function updateMarker(type, lat, lon) {
    // Remove existing marker if present
    if (state.markers[type]) {
      map.removeLayer(state.markers[type]);
    }

    // Create new marker
    const marker = L.marker([lat, lon], {
      title: type === "origin" ? "Origin" : "Destination"
    }).addTo(map);

    state.markers[type] = marker;

    // Pan to marker
    map.setView([lat, lon], 14);
  }

  function setupAutocomplete(inputId, resultsId) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);

    const debouncedSearch = debounce(async (query) => {
      const geocodeResults = await fetchGeocodeResults(query);
      renderAutocompleteResults(geocodeResults, results);
    }, DEBOUNCE_MS);

    input.addEventListener("input", (e) => {
      debouncedSearch(e.target.value);
    });

    // Close dropdown when clicking outside
    document.addEventListener("click", (e) => {
      if (!input.contains(e.target) && !results.contains(e.target)) {
        results.classList.remove("active");
      }
    });
  }

  function show() {
    const modal = document.getElementById("commute-modal");
    modal.classList.add("active");
  }

  function hide() {
    const modal = document.getElementById("commute-modal");
    modal.classList.remove("active");

    // Clear inputs
    document.getElementById("origin-input").value = "";
    document.getElementById("destination-input").value = "";
    document.getElementById("origin-results").innerHTML = "";
    document.getElementById("destination-results").innerHTML = "";
  }

  function init() {
    setupAutocomplete("origin-input", "origin-results");
    setupAutocomplete("destination-input", "destination-results");

    // Close modal button
    const closeBtn = document.getElementById("commute-modal-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", hide);
    }

    // Close on backdrop click
    const modal = document.getElementById("commute-modal");
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        hide();
      }
    });
  }

  return { show, hide, init, state };
}
