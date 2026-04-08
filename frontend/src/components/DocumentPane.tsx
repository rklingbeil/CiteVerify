import { useEffect, useRef } from "react";
import type { CitationReport, VerificationReport } from "../types";
import styles from "./DocumentPane.module.css";

interface Props {
  report: VerificationReport;
  selectedIndex: number | null;
  onSelectCitation: (index: number) => void;
}

/** Map verification status to a CSS module class name */
function statusClass(status: string): string {
  switch (status) {
    case "verified": return styles.verified!;
    case "warning": return styles.warning!;
    case "error": return styles.error!;
    default: return styles.unverifiable!;
  }
}

/**
 * Builds an array of text segments, alternating between plain text and citation highlights.
 * Citations are sorted by position_start and non-overlapping regions are highlighted.
 */
function buildSegments(
  text: string,
  citations: CitationReport[],
): Array<{ text: string; citationIndex: number | null }> {
  // Sort citations by position_start
  const sorted = citations
    .map((c, i) => ({ ...c, originalIndex: i }))
    .filter((c) => c.extraction.position_start >= 0 && c.extraction.position_end > c.extraction.position_start)
    .sort((a, b) => a.extraction.position_start - b.extraction.position_start);

  const segments: Array<{ text: string; citationIndex: number | null }> = [];
  let cursor = 0;

  for (const c of sorted) {
    const start = Math.max(c.extraction.position_start, cursor);
    const end = Math.min(c.extraction.position_end, text.length);
    if (start >= end) continue;

    // Plain text before this citation
    if (cursor < start) {
      segments.push({ text: text.slice(cursor, start), citationIndex: null });
    }

    // Citation highlight
    segments.push({ text: text.slice(start, end), citationIndex: c.originalIndex });
    cursor = end;
  }

  // Remaining text
  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), citationIndex: null });
  }

  return segments;
}

export default function DocumentPane({ report, selectedIndex, onSelectCitation }: Props) {
  const selectedRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (selectedRef.current) {
      selectedRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [selectedIndex]);

  const segments = buildSegments(report.document_text, report.citations);

  return (
    <div className={styles.pane}>
      <div className={styles.content}>
        {segments.map((seg, i) => {
          if (seg.citationIndex === null) {
            return <span key={i}>{seg.text}</span>;
          }

          const citation = report.citations[seg.citationIndex]!;
          const isSelected = seg.citationIndex === selectedIndex;

          return (
            <span
              key={i}
              ref={isSelected ? selectedRef : undefined}
              className={`${styles.highlight} ${statusClass(citation.verification.status)} ${isSelected ? styles.selected : ""}`}
              onClick={() => onSelectCitation(seg.citationIndex!)}
              title={`${citation.extraction.case_name} — ${citation.verification.status}`}
            >
              {seg.text}
            </span>
          );
        })}
      </div>
    </div>
  );
}
