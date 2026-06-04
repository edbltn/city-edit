import { useState, useCallback, useEffect, useLayoutEffect, useRef, memo } from "react";
import { useRoute, useTheme, useMap } from "../../context";
import { getSuggestionsForTheme } from "../../constants/voteTypes";
import { mapVoteTypesForPointType } from "../../map/runtime";
import { iconSrc, iconForLabel } from "../../themes";
import { suggestionGlyphForLabel } from "../../utils/suggestionIcon";
import { CheckIcon } from "../CheckIcon";
import { noAutofillProps } from "../../utils/noAutofill";
import "./VoteTypeSelector.css";

export const VoteTypeSelector = memo(function VoteTypeSelector() {
  const { voteType, setVoteType, pointType } = useRoute();
  const theme = useTheme();
  const map = useMap();
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  // Upper bound for the (left-anchored) dropdown so it grows to the viewport's
  // right edge then ellipsizes, never overflowing. Measured from the box's left.
  const [dropdownMaxWidth, setDropdownMaxWidth] = useState<number>();
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputValueRef = useRef("");
  // The committed vote type when the field opened, restored if editing is cancelled.
  const previousVoteTypeRef = useRef(voteType);

  // Prefer the resolved map's server-provided vote-type list; fall back to theme.
  const suggestions = map?.voteTypes?.length
    ? mapVoteTypesForPointType(map, pointType)
    : getSuggestionsForTheme(theme, pointType);

  const inputLower = inputValue.trim().toLowerCase();
  const filteredSuggestions = inputLower
    ? suggestions.filter((s) => s.label.toLowerCase().includes(inputLower))
    : suggestions;

  // Custom vote types already voted on this map. They surface only while the
  // user is typing — never in the default list — and carry no themed icon, so
  // they render with the colorized suggestion glyph.
  const extraLabels = inputLower
    ? (map?.searchVoteTypes ?? []).filter(
        (lbl) =>
          lbl.toLowerCase().includes(inputLower) &&
          !suggestions.some((s) => s.label.toLowerCase() === lbl.toLowerCase())
      )
    : [];

  const hasExactMatch =
    suggestions.some((s) => s.label.toLowerCase() === inputLower) ||
    extraLabels.some((lbl) => lbl.toLowerCase() === inputLower);
  // Maps that disallow user suggestions can't add custom vote types.
  const allowCustom = map ? map.allowSuggestions : true;
  const showCustomOption = allowCustom && inputLower !== "" && !hasExactMatch;

  // When suggestions are off and there's a single choice, there's nothing to
  // pick — show a frozen chip, not a dropdown. "Single choice" covers two cases:
  //   1. the map authored exactly one vote type overall, or
  //   2. the pointType-filtered list happens to have one entry.
  // Case 1 must ignore pointType: a route-only single type is filtered out of
  // `suggestions` while in point mode (before both endpoints are set), which
  // previously left the field unlocked until a full route was drawn. Keying the
  // lock off the map's full list keeps it frozen in every mode.
  const mapVoteTypes = map?.voteTypes ?? [];
  const frozenLabel = !allowCustom
    ? mapVoteTypes.length === 1
      ? mapVoteTypes[0].label
      : suggestions.length === 1
        ? suggestions[0].label
        : null
    : null;

  const options = [
    ...filteredSuggestions.map((s) => ({
      label: s.label,
      icon: s.icon,
      glyph: false,
      isCustom: false,
    })),
    ...extraLabels.map((lbl) => ({
      label: lbl,
      icon: "",
      glyph: true,
      isCustom: false,
    })),
    ...(showCustomOption
      ? [{ label: inputValue.trim(), icon: "", glyph: false, isCustom: true }]
      : []),
  ];

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
    (label: string) => {
      setVoteType(label);
      setInputValue("");
      inputValueRef.current = "";
      setIsOpen(false);
      inputRef.current?.blur();
    },
    [setVoteType]
  );

  const handleFocus = useCallback(() => {
    // Remember the committed type so Escape / empty-close can revert to it.
    previousVoteTypeRef.current = voteType;
    setIsOpen(true);
    setInputValue("");
    inputValueRef.current = "";
    setHighlightedIndex(0);
  }, [voteType]);

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
        setHighlightedIndex((prev) => Math.min(prev + 1, options.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlightedIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (options.length > 0) {
          const opt = options[highlightedIndex];
          if (opt) selectOption(opt.label);
        } else if (inputValue.trim()) {
          selectOption(inputValue.trim());
        }
      } else if (e.key === "Escape") {
        setVoteType(previousVoteTypeRef.current);
        setIsOpen(false);
        setInputValue("");
        inputValueRef.current = "";
        inputRef.current?.blur();
      }
    },
    [options, highlightedIndex, inputValue, selectOption, setVoteType]
  );

  const handleChevronClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (isOpen) {
      const trimmed = inputValueRef.current.trim();
      if (trimmed) {
        selectOption(trimmed);
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
          selectOption(trimmed);
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

  // The committed type's themed icon: the map's own vote-type icon wins (user
  // maps define their own), then a matching preset icon. Null → it's a custom
  // type and renders the colorized suggestion glyph instead.
  const displayIcon = voteType ? iconForLabel(voteType, map?.voteTypes) : null;

  // A single, fixed vote type renders a static chip (no input/chevron/dropdown);
  // the vote type defaults to it via getDefaultVoteTypeForTheme, so casting works.
  const frozenIcon = frozenLabel ? iconForLabel(frozenLabel, map?.voteTypes) : null;

  return (
    <div
      ref={containerRef}
      className={`vote-type-selector ${frozenLabel ? "vote-type-frozen" : isOpen ? "active" : ""}`}
      title={frozenLabel ?? undefined}
    >
      {frozenLabel ? (
        <div className="vote-type-control">
          {frozenIcon ? (
            <img className="vote-type-icon-img" src={iconSrc(frozenIcon)} alt="" />
          ) : (
            <span
              className="vote-type-icon-img"
              dangerouslySetInnerHTML={{ __html: suggestionGlyphForLabel(frozenLabel, 18) }}
            />
          )}
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
            {displayIcon ? (
              <img className="vote-type-icon-img" src={iconSrc(displayIcon)} alt="" />
            ) : (
              <span
                className="vote-type-icon-img"
                dangerouslySetInnerHTML={{ __html: suggestionGlyphForLabel(voteType, 18) }}
              />
            )}
            <span className="vote-type-display-text">{voteType}</span>
          </div>
        )}
        {!isOpen && !voteType && (
          <div className="vote-type-placeholder">
            {allowCustom ? "Suggest a change..." : "Select a vote type"}
          </div>
        )}
        <span className="vote-type-chevron" onMouseDown={handleChevronClick}>
          <span className="caret-down" />
        </span>
      </div>

      {isOpen && options.length > 0 && (
        <div
          className="vote-type-dropdown"
          style={dropdownMaxWidth ? { maxWidth: dropdownMaxWidth } : undefined}
        >
          {options.map((option, index) => {
            const isHighlighted = index === highlightedIndex;
            const isSelected = option.label === voteType;

            if (option.isCustom) {
              return (
                <div
                  key="custom"
                  className={`vote-type-option vote-type-custom ${isHighlighted ? "highlighted" : ""}`}
                  onMouseDown={() => selectOption(option.label)}
                  onMouseEnter={() => setHighlightedIndex(index)}
                >
                  <span>Suggest: <em>"{option.label}"</em></span>
                </div>
              );
            }

            return (
              <div
                key={option.label}
                className={`vote-type-option ${isHighlighted ? "highlighted" : ""} ${isSelected ? "selected" : ""}`}
                onMouseDown={() => selectOption(option.label)}
                onMouseEnter={() => setHighlightedIndex(index)}
              >
                {option.icon ? (
                  <img className="vote-type-icon-img" src={iconSrc(option.icon)} alt="" />
                ) : option.glyph ? (
                  <span
                    className="vote-type-icon-img"
                    dangerouslySetInnerHTML={{ __html: suggestionGlyphForLabel(option.label, 18) }}
                  />
                ) : null}
                <span className="vote-type-label">{option.label}</span>
                {isSelected && <span className="check-icon"><CheckIcon size={11} /></span>}
              </div>
            );
          })}
        </div>
      )}
      </>
      )}
    </div>
  );
});
