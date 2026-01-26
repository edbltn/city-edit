import { memo, useEffect } from "react";
import { createPortal } from "react-dom";
import "./HowItWorksModal.css";

interface Props {
  isOpen: boolean;
  onClose: () => void;
}

// Inline presentational components for step references
const ModeExample = () => (
  <span className="inline-btn-example">
    <span className="mode-icon">🚴</span> Bike
  </span>
);

const StartDot = () => (
  <span className="step-icon-slot">
    <span className="legend-marker legend-marker-start"></span>
  </span>
);

const EndDot = () => (
  <span className="step-icon-slot">
    <span className="legend-marker legend-marker-end"></span>
  </span>
);

const DesireLine = () => (
  <span className="step-icon-slot">
    <span className="legend-line legend-line-desire"></span>
  </span>
);

const VoteButtonExample = () => (
  <span className="inline-btn-example vote-example">Cast Vote</span>
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
                <span>Click again to set your destination</span>
              </li>
              <li>
                <DesireLine />
                <span>Drag your desired path to match your ideal route</span>
              </li>
              <li>
                <span className="step-icon-slot">
                  <VoteButtonExample />
                </span>
                <span>Cast your vote to add to the heatmap</span>
              </li>
            </ul>
          </section>

          <section className="how-it-works-section">
            <h3>Fair Voting</h3>
            <p>
              The more routes you vote on, the more your total contribution
              gets diluted. That way, everyone gets equal representation!
            </p>
          </section>
        </div>
      </div>
    </div>
  );

  return createPortal(modalContent, document.body);
});
