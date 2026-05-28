import { useState, useCallback, useEffect, useRef, memo } from "react";
import { useRoute, useTheme } from "../../context";
import { getSuggestionsForTheme } from "../../constants/voteTypes";
import { iconSrc, iconForLabel } from "../../themes";
import "./VoteTypeSelector.css";

export const VoteTypeSelector = memo(function VoteTypeSelector() {
  const { voteType, setVoteType, pointType } = useRoute();
  const theme = useTheme();
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const suggestions = getSuggestionsForTheme(theme, pointType);

  const inputLower = inputValue.trim().toLowerCase();
  const filteredSuggestions = inputLower
    ? suggestions.filter((s) => s.label.toLowerCase().includes(inputLower))
    : suggestions;

  const hasExactMatch = suggestions.some(
    (s) => s.label.toLowerCase() === inputLower
  );
  const showCustomOption = inputLower !== "" && !hasExactMatch;

  const options = [
    ...filteredSuggestions.map((s) => ({ label: s.label, icon: s.icon, isCustom: false })),
    ...(showCustomOption ? [{ label: inputValue.trim(), icon: "", isCustom: true }] : []),
  ];

  useEffect(() => {
    setHighlightedIndex(0);
  }, [inputValue]);

  const selectOption = useCallback(
    (label: string) => {
      setVoteType(label);
      setInputValue("");
      setIsOpen(false);
      inputRef.current?.blur();
    },
    [setVoteType]
  );

  const handleFocus = useCallback(() => {
    setIsOpen(true);
    setInputValue("");
    setHighlightedIndex(0);
  }, []);

  const handleBlur = useCallback(() => {}, []);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setInputValue(e.target.value);
    setIsOpen(true);
  }, []);

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
          selectOption(options[highlightedIndex].label);
        } else if (inputValue.trim()) {
          selectOption(inputValue.trim());
        }
      } else if (e.key === "Escape") {
        setIsOpen(false);
        setInputValue("");
        inputRef.current?.blur();
      }
    },
    [options, highlightedIndex, inputValue, selectOption]
  );

  const handleChevronClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (isOpen) {
      setIsOpen(false);
      setInputValue("");
      inputRef.current?.blur();
    } else {
      inputRef.current?.focus();
    }
  }, [isOpen]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setInputValue("");
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

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
                className={`vote-type-option ${isHighlighted ? "highlighted" : ""} ${isSelected ? "selected" : ""}`}
                onMouseDown={() => selectOption(option.label)}
                onMouseEnter={() => setHighlightedIndex(index)}
              >
                {option.icon && <img className="vote-type-icon-img" src={iconSrc(option.icon)} alt="" />}
                <span className="vote-type-label">{option.label}</span>
                {isSelected && <span className="check-icon">{"✓"}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});
