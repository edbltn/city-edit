import { IconQuestion, IconArticle, IconInfo, IconBubble, IconHeart } from "./icons";
import "./NavRail.css";

export const BLOG_URL = "https://sphericalharmonics.substack.com/";
export const ABOUT_URL = "https://sphericalharmonics.org/";
export const FEEDBACK_URL = "https://feedback.cityedit.org";
export const DONATE_URL = "https://donate.cityedit.org";

interface NavRailProps {
  /**
   * Opens the How-it-Works modal. Only the map header passes this — the glyph
   * is about *this map's* controls, so it has nothing to say on the landing
   * page and is dropped there rather than shown inert.
   */
  onHowItWorks?: () => void;
  className?: string;
}

/**
 * Secondary/meta nav — How it Works · Blog · About | Feedback · Donate.
 *
 * Shared by the map topbar and the landing header so there's one place these
 * links live. Two enclosed segments carry the semantics: the first group is
 * things to *read*, the second is things to *do*. Everything is icon-only
 * except Donate, which keeps its label and the map's accent fill — this rail
 * is deliberately the quiet half of the header, and Donate is the one item in
 * it worth spending attention on.
 */
export function NavRail({ onHowItWorks, className }: NavRailProps) {
  return (
    <nav className={`nav-rail${className ? ` ${className}` : ""}`} aria-label="Site links">
      <div className="nav-group">
        {onHowItWorks && (
          <button
            type="button"
            className="nav-btn"
            onClick={onHowItWorks}
            aria-label="How it works"
            data-tip="How it works"
          >
            <IconQuestion />
          </button>
        )}

        <a
          className="nav-btn"
          href={BLOG_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Blog"
          data-tip="Blog"
        >
          <IconArticle />
        </a>

        <a
          className="nav-btn"
          href={ABOUT_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="About"
          data-tip="About"
        >
          <IconInfo />
        </a>
      </div>

      <div className="nav-group">
        <a
          className="nav-btn"
          href={FEEDBACK_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Feedback"
          data-tip="Feedback"
        >
          <IconBubble />
        </a>

        <a
          className="nav-btn nav-btn-donate"
          href={DONATE_URL}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Donate"
          data-tip="Donate"
        >
          <IconHeart />
          <span className="nav-btn-label">Donate</span>
        </a>
      </div>
    </nav>
  );
}
