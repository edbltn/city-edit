import { useState, useCallback, useEffect, useRef, memo } from "react";
import { useRoute, useTheme } from "../../context";
import { getSuggestionsForTheme } from "../../constants/voteTypes";
import "./VoteTypeSelector.css";

// Parse emoji and text from a vote type label
// Matches emoji at start (including compound emojis with variation selectors)
function parseLabel(label: string): { emoji: string; text: string } {
  // Match any emoji sequence at the start followed by whitespace
  const match = label.match(/^([\p{Emoji}\p{Emoji_Modifier}\p{Emoji_Component}\p{Emoji_Modifier_Base}\p{Emoji_Presentation}\u{FE0F}\u{200D}]+)\s+(.*)$/u);
  if (match) {
    return { emoji: match[1], text: match[2] };
  }
  // Fallback: try to split on first space if first char looks like emoji
  const firstSpace = label.indexOf(' ');
  if (firstSpace > 0 && firstSpace <= 4) {
    const potentialEmoji = label.slice(0, firstSpace);
    // Check if it's likely an emoji (non-ASCII start)
    if (potentialEmoji.codePointAt(0)! > 127) {
      return { emoji: potentialEmoji, text: label.slice(firstSpace + 1) };
    }
  }
  return { emoji: "", text: label };
}

export const VoteTypeSelector = memo(function VoteTypeSelector() {
  const { voteType, setVoteType, pointType } = useRoute();
  const theme = useTheme();
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const suggestions = getSuggestionsForTheme(theme, pointType);

  // Filter suggestions based on input
  const inputLower = inputValue.trim().toLowerCase();
  const filteredSuggestions = inputLower
    ? suggestions.filter((s) => {
        const { text } = parseLabel(s.label);
        return text.toLowerCase().includes(inputLower);
      })
    : suggestions;

  // Show custom option when input doesn't exactly match any suggestion
  const hasExactMatch = suggestions.some((s) => {
    const { text } = parseLabel(s.label);
    return text.toLowerCase() === inputLower || s.label.toLowerCase() === inputLower;
  });
  const showCustomOption = inputLower !== "" && !hasExactMatch;

  // Build options list: filtered suggestions + custom option at bottom
  const options = [
    ...filteredSuggestions.map((s) => ({ label: s.label, isCustom: false })),
    ...(showCustomOption ? [{ label: inputValue.trim(), isCustom: true }] : []),
  ];

  // Reset highlight when options change
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

  const handleBlur = useCallback(() => {
    // Don't close on blur - only close on outside click or selection
  }, []);

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

  // Close on outside click
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

  // Display value parsing
  const { emoji: displayEmoji, text: displayText } = parseLabel(voteType);

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
        />
        {!isOpen && voteType && (
          <div className="vote-type-display">
            {displayEmoji && <span className="vote-type-icon">{displayEmoji}</span>}
            <span className="vote-type-display-text">{displayText}</span>
          </div>
        )}
        {!isOpen && !voteType && (
          <div className="vote-type-placeholder">What should be added here?</div>
        )}
        <span className="vote-type-chevron" onMouseDown={handleChevronClick}>
          &#9662;
        </span>
      </div>

      {isOpen && options.length > 0 && (
        <div className="vote-type-dropdown">
          {options.map((option, index) => {
            const { emoji, text } = parseLabel(option.label);
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
                  <span><strong>Suggest:</strong> <em>"{text}"</em></span>
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
                <span className="vote-type-icon">{emoji}</span>
                <span className="vote-type-label">{text}</span>
                {isSelected && <span className="check-icon">{"\u2713"}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});
