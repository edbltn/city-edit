import { memo, useEffect } from "react";
import { createPortal } from "react-dom";
import { COLOR_START, COLOR_END } from "../../colors";
import { iconSrc } from "../../themes";
import { requestOnboarding } from "../../onboarding/active";
import "./HowItWorksModal.css";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

// Inline presentational components for step references
const ModeExample = () => (
  <span className="inline-btn-example">
    <img className="mode-icon-img" src={iconSrc("bikes")} alt="" /> Bike
  </span>
);

const KitePin = ({ color }: { color: string }) => (
  <span className="step-icon-slot">
    <span className="hiw-kite" style={{ color }}>
      <span className="hiw-kite-diamond">◆</span>
      <span className="hiw-kite-stem" style={{ background: color }}></span>
    </span>
  </span>
);

const StartDot = () => <KitePin color={COLOR_START} />;
const EndDot = () => <KitePin color={COLOR_END} />;

const DesireLine = () => (
  <span className="step-icon-slot">
    <span className="hiw-desire-line"></span>
  </span>
);

const VoteForExample = () => (
  <span className="inline-btn-example hiw-vote-dropdown"><span className="caret-down" /></span>
);

const CastExample = () => (
  <span className="inline-btn-example hiw-cast-example">
    <span className="hiw-cast-btn" style={{ color: COLOR_END }}>−</span>
    <span className="hiw-cast-btn" style={{ color: COLOR_START }}>+</span>
  </span>
);

export const HowItWorksModal = memo(function HowItWorksModal({ isOpen, onClose }: Props) {
  // Prevent body scroll when modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const handleBackdropClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onClose();
  };

  const modalContent = (
    <div className="modal active" onClick={handleBackdropClick}>
      <div className="modal-content how-it-works-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>How it Works</h2>
          <button className="modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="modal-body">
          <section className="how-it-works-section">
            <h3>The Concept</h3>
            <p>
              A <a
                href="https://en.wikipedia.org/wiki/Desire_path"
                target="_blank"
                rel="noopener noreferrer"
                className="wiki-link"
              >desire path</a> is a trail worn into the ground by footfall — the
              natural route people choose when sidewalks don't go where they need.
            </p>
          </section>

          <section className="how-it-works-section">
            <h3>The Vision</h3>
            <p>
              This map crowdsources the urban equivalent of desire paths. It lets
              you project alternative routes (in a perfect world where no
              infrastructural constraints exist) and vote for the route you'd most
              want to see. Every vote for a desired commute shapes the heatmap.
            </p>
          </section>

          {/* The way back into the first-run wall — which opens by itself on a
              person's first map and never again, so this is the only way back to
              it. It exists because the flow's "have you been here before?" key is
              the IP hash, which deliberately treats a new person on a network
              somebody else has used as a returning visitor
              (onboarding/firstRun.ts). Rather than loosen a key that was chosen
              to stop real users being onboarded twice, the wall is one click
              away for anyone who wants it. */}
          <section className="how-it-works-section">
            <h3>Not sure where to start?</h3>
            <p>
              Say what needs fixing, then point at the place on the map — it walks
              you through one vote from start to cast.
            </p>
            <button
              type="button"
              className="hiw-start-sentence"
              onClick={() => {
                requestOnboarding();
                onClose();
              }}
            >
              What needs fixing?
            </button>
          </section>

          <section className="how-it-works-section">
            <h3>How to Use</h3>
            <ul className="how-it-works-steps">
              <li>
                <span className="step-icon-slot">
                  <ModeExample />
                </span>
                <span>Choose your mode</span>
              </li>
              <li>
                <StartDot />
                <span>Click the map to set your start</span>
              </li>
              <li>
                <EndDot />
                <span>Click again to set your destination (or skip for point votes)</span>
              </li>
              <li>
                <DesireLine />
                <span>Drag your desired path to match your ideal route</span>
              </li>
              <li>
                <span className="step-icon-slot">
                  <VoteForExample />
                </span>
                <span>Choose what you're voting for (or type your own idea)</span>
              </li>
              <li>
                <span className="step-icon-slot">
                  <CastExample />
                </span>
                <span>
                  Cast <strong>+</strong> for or <strong>−</strong> against — re-click
                  the same button to undo your vote
                </span>
              </li>
            </ul>
          </section>

          <section className="how-it-works-section">
            <h3>Vote Types</h3>
            <p>
              <strong>Route votes</strong>: Vote for infrastructure along a path —
              bike lanes, crosswalks, or car lanes.
            </p>
            <p>
              <strong>Point votes</strong>: Vote for amenities at a specific location —
              Citi Bike stations, benches, parking, trees, or EV chargers.
            </p>
            <p>
              You can also type any custom suggestion you like!
            </p>
            <p>
              <strong>For or against</strong>: cast <span style={{ color: COLOR_START, fontWeight: 700 }}>+</span> to
              support a proposal along your whole selection, or <span style={{ color: COLOR_END, fontWeight: 700 }}>−</span> to
              push back on it. Click the same button again to remove your vote.
            </p>
          </section>

          <section className="how-it-works-section">
            <h3>Fair Voting</h3>
            <p>
              The more routes you vote on, the more your total contribution
              gets diluted (that way, everyone gets equal representation in the heatmap)
            </p>
          </section>
        </div>
      </div>
    </div>
  );

  return createPortal(modalContent, document.body);
});
