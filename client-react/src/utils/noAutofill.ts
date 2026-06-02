// Chrome (and password managers) cluster nearby text/password inputs and offer
// to save them as logins, ID cards, or addresses. None of our inputs are
// credentials, so we actively opt out. `autoComplete="off"` alone is widely
// ignored by Chrome's heuristics, so we also stamp the data-* hints that the
// major password managers respect and give each field a neutral name.
export const noAutofillProps = {
  autoComplete: "off",
  "data-1p-ignore": "",       // 1Password
  "data-lpignore": "true",    // LastPass
  "data-form-type": "other",  // Dashlane
  "data-bwignore": "true",    // Bitwarden
} as const;
