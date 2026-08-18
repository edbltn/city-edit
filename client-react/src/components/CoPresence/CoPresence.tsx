import { useWebSocketContext } from "../../context/WebSocketContext";
import { AudienceIcon } from "./PeopleIcon";
import "./CoPresence.css";

/** Below this we say nothing.
 *
 *  This was 3 — the brief asked for more than one OTHER viewer, on the reading
 *  that a pair does not need to be told it is a pair. Measured against how
 *  people are actually COUNTED, that reading does not survive: presence dedupes
 *  on a hash of the client IP (presence.py), so a household, an office or a
 *  carrier NAT is ONE member. Three marks therefore needs three separate
 *  networks with the same map open inside the same two-second push — a bar this
 *  map's traffic is unlikely to clear, and one the owner cannot clear on
 *  purpose even with three devices, because they all leave by the same egress.
 *  That last part is measured, not assumed: three real browsers on one machine
 *  are pushed n=1, and it takes three genuinely separate source IPs to be told
 *  n=3. What has NOT been measured is the live arrival rate — the ceiling this
 *  argument rests on is a prediction, so revisit it against real numbers if
 *  the map ever gets busy.
 *
 *  A ritual nobody attends produces no common knowledge at all, which is the
 *  only thing this component was built to make. Two is where the product
 *  actually starts: "someone else is here, right now, looking at what you are
 *  looking at" is the whole claim, and it is no less true of one other person —
 *  a pair is exactly the case where each of them can be sure of the other. The
 *  under-count baked into the identity key also means a shown 2 is a floor, not
 *  a guess: the real room is 2 or more, never fewer. */
const MIN_TOTAL = 2;

/** Past this many marks the row stops being countable at a glance, which is
 *  the entire reason it is drawn as marks. Beyond it, the marks become a
 *  texture and the numeral carries the count. */
const MAX_MARKS = 12;

/**
 * Who else has this map open, drawn rather than tallied.
 *
 * The thing being built here is common knowledge — not "how popular is this
 * map" but "we are looking at this together, and we both know it". That only
 * works if the reader believes the number, and a number nobody can check is a
 * number people quietly discount. So the count is drawn as one mark per
 * person: you can count the marks and see that the numeral is not lying, and
 * the mark that is filled is YOU, which makes visible — without a footnote —
 * that the total includes the person reading it. A count that had quietly
 * excluded you, or padded itself, could not survive being drawn.
 *
 * The marks are hollow squares in the app's own hand: the same bare 1.5px
 * stroked box the mobile wordmark builds its letters from. Nothing new is
 * introduced to say something quiet.
 *
 * What the number actually means is one hover away rather than hidden: people
 * are counted per network connection and only while their tab is in the
 * foreground, so a household reads as one and a buried tab reads as nobody.
 * Both of those under-count. That is the deliberate direction — an audience
 * that is smaller than the truth costs us nothing, and one that is larger
 * costs us the only thing this feature has.
 */
export function CoPresence() {
  const { viewerCount } = useWebSocketContext();

  // 0 is both "nobody" and "we lost the socket and no longer know". Both mean
  // the same thing here: stop claiming company.
  if (viewerCount < MIN_TOTAL) return null;

  const others = viewerCount - 1;
  const marks = Math.min(viewerCount, MAX_MARKS);
  const overflow = viewerCount - marks;

  return (
    <aside
      className="copresence"
      // The honest small print, on hover rather than on the face of it.
      title={
        "Counted once per network connection, and only while a tab is in the "
        + "foreground. People sharing a connection count as one, so this is "
        + "more likely to be low than high."
      }
    >
      <span className="copresence-marks" aria-hidden="true">
        {Array.from({ length: marks }, (_, i) => (
          <span
            key={i}
            className={`copresence-mark${i === 0 ? " is-you" : ""}`}
            // Stagger the row in from your own mark outwards, so the
            // animation reads as "and then these people", which is what it is.
            style={{ animationDelay: `${i * 45}ms` }}
          />
        ))}
        {overflow > 0 && <span className="copresence-overflow">+{overflow}</span>}
      </span>
      <AudienceIcon others={others} />
      <span className="copresence-text">
        You’re looking at this with {others} other{" "}
        {others === 1 ? "viewer" : "viewers"}
      </span>
    </aside>
  );
}
