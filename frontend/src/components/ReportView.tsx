import type { CitationReport, VerificationReport } from "../types";
import SummaryBar from "./SummaryBar";
import styles from "./ReportView.module.css";

interface Props {
  report: VerificationReport;
  onReset: () => void;
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

function confidencePercent(c: number): string {
  return `${Math.round(c * 100)}%`;
}

function CitationRow({ citation, index }: { citation: CitationReport; index: number }) {
  const { extraction, lookup, verification } = citation;
  const status = verification.status;

  return (
    <div className={`${styles.row} ${styles[status] || ""}`}>
      <div className={styles.rowHeader}>
        <span className={styles.rowNum}>{index + 1}</span>
        <span className={`${styles.statusBadge} ${styles[`badge_${status}`]}`}>
          {statusLabel(status)}
        </span>
        <span className={styles.confidence}>
          {verification.confidence > 0 ? confidencePercent(verification.confidence) + " confidence" : ""}
        </span>
      </div>

      <div className={styles.citationMain}>
        <div className={styles.caseName}>{extraction.case_name}</div>
        <div className={styles.citeText}>{extraction.citation_text}</div>
      </div>

      {/* Source lookup */}
      <div className={styles.section}>
        <div className={styles.sectionLabel}>Source</div>
        <div className={styles.sectionContent}>
          {lookup.found ? (
            <>
              <span className={styles.sourceFound}>Found via {lookup.source}</span>
              {lookup.court && <span className={styles.detail}> &mdash; {lookup.court}</span>}
              {lookup.date_filed && <span className={styles.detail}>, {lookup.date_filed}</span>}
              {lookup.url && (
                <a href={lookup.url} target="_blank" rel="noopener noreferrer" className={styles.link}>
                  View source
                </a>
              )}
            </>
          ) : (
            <span className={styles.notFound}>Source not found ({lookup.status})</span>
          )}
        </div>
      </div>

      {/* Quote verification */}
      {extraction.quoted_text && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Quote</div>
          <div className={styles.sectionContent}>
            <div className={styles.quote}>&ldquo;{extraction.quoted_text}&rdquo;</div>
            {verification.quote_accuracy && (
              <div className={styles.finding}>
                <strong>Accuracy:</strong> {verification.quote_accuracy}
                {verification.quote_diff && <span> &mdash; {verification.quote_diff}</span>}
              </div>
            )}
            {verification.actual_quote && (
              <div className={styles.finding}>
                <strong>Actual text:</strong>{" "}
                <span className={styles.actualQuote}>&ldquo;{verification.actual_quote}&rdquo;</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Characterization verification */}
      {extraction.characterization && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Characterization</div>
          <div className={styles.sectionContent}>
            <div className={styles.charText}>{extraction.characterization}</div>
            {verification.characterization_accuracy && (
              <div className={styles.finding}>
                <strong>Assessment:</strong> {verification.characterization_accuracy}
                {verification.characterization_explanation && (
                  <span> &mdash; {verification.characterization_explanation}</span>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Verification reasoning */}
      {verification.reasoning && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Verification Reasoning</div>
          <div className={styles.reasoningText}>{verification.reasoning}</div>
        </div>
      )}

      {/* Context from document */}
      {extraction.context && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Context in Document</div>
          <div className={styles.contextText}>{extraction.context}</div>
        </div>
      )}
    </div>
  );
}

export default function ReportView({ report, onReset }: Props) {
  return (
    <div className={styles.wrapper}>
      <SummaryBar report={report} onReset={onReset} />
      <div className={styles.content}>
        {report.citations.length === 0 ? (
          <div className={styles.empty}>No citations found in this document.</div>
        ) : (
          <div className={styles.citationList}>
            {report.citations.map((c, i) => (
              <CitationRow key={i} citation={c} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
