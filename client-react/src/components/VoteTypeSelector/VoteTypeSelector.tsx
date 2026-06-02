import { useState, useCallback, useEffect, useRef, memo } from "react";
import { useRoute, useTheme, useMap } from "../../context";
import { getSuggestionsForTheme } from "../../constants/voteTypes";
import { iconSrc, iconForLabel } from "../../themes";
import { CheckIcon } from "../CheckIcon";
import "./VoteTypeSelector.css";

export const VoteTypeSelector = memo(function VoteTypeSelector() {
  const { voteType, setVoteType, pointType, isVoteTypeAlreadyCast } = useRoute();
  const theme = useTheme();
  const map = useMap();
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputValueRef = useRef("");
  // The committed vote type when the field opened, restored if editing is cancelled.
  const previousVoteTypeRef = useRef(voteType);

  // Prefer the resolved map's server-provided vote-type list; fall back to theme.
  const suggestions = map?.voteTypes?.length
    ? map.voteTypes.filter((s) => s.pointType === pointType)
    : getSuggestionsForTheme(theme, pointType);

  const inputLower = inputValue.trim().toLowerCase();
  const filteredSuggestions = inputLower
    ? suggestions.filter((s) => s.label.toLowerCase().includes(inputLower))
    : suggestions;

  const hasExactMatch = suggestions.some(
    (s) => s.label.toLowerCase() === inputLower
  );
  // Maps that disallow user suggestions can't add custom vote types.
  const allowCustom = map ? map.allowSuggestions : true;
  const showCustomOption = allowCustom && inputLower !== "" && !hasExactMatch;

  const options = [
    ...filteredSuggestions.map((s) => ({
      label: s.label,
      icon: s.icon,
      isCustom: false,
      alreadyVoted: isVoteTypeAlreadyCast(s.label),
    })),
    ...(showCustomOption
      ? [{ label: inputValue.trim(), icon: "", isCustom: true, alreadyVoted: false }]
      : []),
  ];

  useEffect(() => {
    setHighlightedIndex(0);
  }, [inputValue]);

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
          if (opt && !opt.alreadyVoted) selectOption(opt.label);
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

  const displayIcon = voteType ? iconForLabel(voteType) : null;

  return (
    <div
      ref={containerRef}
      className={`vote-type-selector ${isOpen ? "active" : ""}`}
    >
      <div className="vote-type-control">
        <input
          ref={inputRef}
          type="text"
          className="vote-type-input"
          value={isOpen ? inputValue : ""}
          onChange={handleInputChange}
          onFocus={handleFocus}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          placeholder={isOpen ? "Type to search..." : ""}
          spellCheck={false}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
        />
        {!isOpen && voteType && (
          <div className="vote-type-display">
            {displayIcon && <img className="vote-type-icon-img" src={iconSrc(displayIcon)} alt="" />}
            <span className="vote-type-display-text">{voteType}</span>
          </div>
        )}
        {!isOpen && !voteType && (
          <div className="vote-type-placeholder">What should be added here?</div>
        )}
        <span className="vote-type-chevron" onMouseDown={handleChevronClick}>
          <span className="caret-down" />
        </span>
      </div>

      {isOpen && options.length > 0 && (
        <div className="vote-type-dropdown">
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
                  <span><strong>Suggest:</strong> <em>"{option.label}"</em></span>
                </div>
              );
            }

            return (
              <div
                key={option.label}
                className={`vote-type-option ${isHighlighted ? "highlighted" : ""} ${isSelected ? "selected" : ""} ${option.alreadyVoted ? "already-voted" : ""}`}
                onMouseDown={() => {
                  if (option.alreadyVoted) return;
                  selectOption(option.label);
                }}
                onMouseEnter={() => setHighlightedIndex(index)}
              >
                {option.icon && <img className="vote-type-icon-img" src={iconSrc(option.icon)} alt="" />}
                <span className="vote-type-label">{option.label}</span>
                {option.alreadyVoted ? (
                  <span className="vote-type-voted-badge">Vote Cast!</span>
                ) : (
                  isSelected && <span className="check-icon"><CheckIcon size={11} /></span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});
