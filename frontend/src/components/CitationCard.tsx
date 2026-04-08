import type { CitationReport } from "../types";
import styles from "./CitationCard.module.css";

interface Props {
  citation: CitationReport;
}

function statusLabel(status: string): string {
  switch (status) {
    case "verified": return "Verified";
    case "warning": return "Warning";
    case "error": return "Error";
    case "unverifiable": return "Unverifiable";
    default: return status;
  }
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case "verified": return styles.badgeVerified!;
    case "warning": return styles.badgeWarning!;
    case "error": return styles.badgeError!;
    default: return styles.badgeUnverifiable!;
  }
}

function accuracyLabel(accuracy: string | null): string {
  if (!accuracy) return "—";
  switch (accuracy) {
    case "exact": return "Exact match";
    case "close": return "Close match";
    case "inaccurate": return "Inaccurate";
    case "accurate": return "Accurate";
    case "misleading": return "Misleading";
    case "unsupported": return "Unsupported";
    default: return accuracy;
  }
}

function confidenceBar(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export default function CitationCard({ citation }: Props) {
  const { extraction, lookup, verification } = citation;

  return (
    <div className={styles.card}>
      {/* Status badge */}
      <div className={styles.statusRow}>
        <span className={`${styles.badge} ${statusBadgeClass(verification.status)}`}>
          {statusLabel(verification.status)}
        </span>
        <span className={styles.confidence}>
          Confidence: {confidenceBar(verification.confidence)}
        </span>
      </div>

      {/* Citation info */}
      <section className={styles.section}>
        <h4 className={styles.sectionTitle}>Citation</h4>
        <p className={styles.citationText}>{extraction.citation_text}</p>
        {extraction.full_reference !== extraction.citation_text && (
          <p className={styles.fullRef}>{extraction.full_reference}</p>
        )}
      </section>

      {/* Source lookup */}
      <section className={styles.section}>
        <h4 className={styles.sectionTitle}>Source</h4>
        {lookup.found ? (
          <div className={styles.sourceInfo}>
            <Row label="Case" value={lookup.case_name} />
            <Row label="Court" value={lookup.court} />
            <Row label="Filed" value={lookup.date_filed} />
            <Row label="Source" value={lookup.source} />
            {lookup.url && (
              <div className={styles.row}>
                <span className={styles.label}>Link</span>
                <a
                  className={styles.link}
                  href={lookup.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  View on {lookup.source === "courtlistener" ? "CourtListener" : "GovInfo"}
                </a>
              </div>
            )}
          </div>
        ) : (
          <p className={styles.notFound}>
            {lookup.status === "error" ? "Lookup failed" : "Case not found in CourtListener or GovInfo"}
          </p>
        )}
      </section>

      {/* Quote verification */}
      {extraction.quoted_text && (
        <section className={styles.section}>
          <h4 className={styles.sectionTitle}>Quote Verification</h4>
          <Row label="Accuracy" value={accuracyLabel(verification.quote_accuracy)} />
          <div className={styles.quoteBlock}>
            <p className={styles.quoteLabel}>Document quotes:</p>
            <blockquote className={styles.quote}>{extraction.quoted_text}</blockquote>
          </div>
          {verification.actual_quote && (
            <div className={styles.quoteBlock}>
              <p className={styles.quoteLabel}>Source text:</p>
              <blockquote className={styles.quote}>{verification.actual_quote}</blockquote>
            </div>
          )}
          {verification.quote_diff && (
            <p className={styles.diff}>{verification.quote_diff}</p>
          )}
        </section>
      )}

      {/* Characterization verification */}
      {extraction.characterization && (
        <section className={styles.section}>
          <h4 className={styles.sectionTitle}>Characterization</h4>
          <Row label="Accuracy" value={accuracyLabel(verification.characterization_accuracy)} />
          <div className={styles.quoteBlock}>
            <p className={styles.quoteLabel}>Document states:</p>
            <blockquote className={styles.quote}>{extraction.characterization}</blockquote>
          </div>
          {verification.characterization_explanation && (
            <p className={styles.explanation}>{verification.characterization_explanation}</p>
          )}
        </section>
      )}

      {/* Context */}
      <section className={styles.section}>
        <h4 className={styles.sectionTitle}>Context</h4>
        <p className={styles.context}>{extraction.context}</p>
      </section>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className={styles.row}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>{value}</span>
    </div>
  );
}
