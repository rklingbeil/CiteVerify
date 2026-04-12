import { useState, useMemo } from "react";
import type { CitationReport, VerificationReport } from "../types";
import styles from "./ReportView.module.css";

interface Props {
  report: VerificationReport;
  onReset: () => void;
}

type SortCol = "num" | "case" | "caseV" | "citeV" | "quoteV" | "charV" | "status" | "conf";
type SortDir = "asc" | "desc";

// ─── Helpers ─────────────────────────────────────────────────

function quoteVerified(c: CitationReport): number {
  if (!c.extraction.quoted_text) return -1; // no quote
  const a = c.verification.quote_accuracy;
  if (a === "exact" || a === "close") return 1;
  if (a === "inaccurate") return 0;
  return -1;
}

function charVerified(c: CitationReport): number {
  if (!c.extraction.characterization) return -1;
  const a = c.verification.characterization_accuracy;
  if (a === "accurate") return 1;
  if (a === "misleading" || a === "unsupported") return 0;
  return -1;
}

function caseVerified(c: CitationReport): number {
  return c.verification.citation_exists ? 1 : 0;
}

function citeVerified(c: CitationReport): number {
  return c.verification.citation_format_correct ? 1 : 0;
}

function CheckIcon({ value }: { value: number }) {
  if (value === 1) return <span className={styles.check}>&#10003;</span>;
  if (value === 0) return <span className={styles.fail}>&#10007;</span>;
  return <span className={styles.gray}>&mdash;</span>;
}

function statusBadgeClass(status: string): string {
  if (status === "error") return styles.statusError;
  if (status === "warning") return styles.statusWarning;
  return "";
}

function rowClass(status: string): string {
  if (status === "error") return styles.rowError;
  if (status === "warning") return styles.rowWarning;
  return "";
}

function truncCase(name: string, max = 28): string {
  if (name.length <= max) return name;
  return name.slice(0, max - 3) + "...";
}

function confidencePercent(c: number): string {
  return `${Math.round(c * 100)}%`;
}

// ─── Main Component ──────────────────────────────────────────

export default function ReportView({ report, onReset }: Props) {
  const [sortCol, setSortCol] = useState<SortCol>("num");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const createdDate = report.created_at
    ? new Date(report.created_at).toISOString().replace("T", " ").slice(0, 16) + " UTC"
    : new Date().toISOString().replace("T", " ").slice(0, 16) + " UTC";

  // Indexed citations for stable numbering
  const indexed = useMemo(
    () => report.citations.map((c, i) => ({ ...c, num: i + 1 })),
    [report.citations],
  );

  // Sorted citations
  const sorted = useMemo(() => {
    const arr = [...indexed];
    arr.sort((a, b) => {
      let va: number | string = 0;
      let vb: number | string = 0;
      switch (sortCol) {
        case "num": va = a.num; vb = b.num; break;
        case "case": va = a.extraction.case_name.toLowerCase(); vb = b.extraction.case_name.toLowerCase(); break;
        case "caseV": va = caseVerified(a); vb = caseVerified(b); break;
        case "citeV": va = citeVerified(a); vb = citeVerified(b); break;
        case "quoteV": va = quoteVerified(a); vb = quoteVerified(b); break;
        case "charV": va = charVerified(a); vb = charVerified(b); break;
        case "status": va = a.verification.status === "error" ? 0 : a.verification.status === "warning" ? 1 : 2; vb = b.verification.status === "error" ? 0 : b.verification.status === "warning" ? 1 : 2; break;
        case "conf": va = a.verification.confidence; vb = b.verification.confidence; break;
      }
      let cmp: number;
      if (typeof va === "string" && typeof vb === "string") {
        cmp = va < vb ? -1 : va > vb ? 1 : 0;
      } else {
        cmp = (va as number) - (vb as number);
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [indexed, sortCol, sortDir]);

  // Flagged citations (errors and warnings) — follow table sort order
  const flagged = useMemo(
    () => sorted.filter((c) => c.verification.status === "error" || c.verification.status === "warning"),
    [sorted],
  );

  // Error case names for summary
  const errorCases = useMemo(
    () => indexed.filter((c) => c.verification.status === "error").map((c) => ({
      name: c.extraction.case_name,
      cite: c.extraction.citation_text,
    })),
    [indexed],
  );

  // Color bar widths
  const total = report.total_citations || 1;
  const barWidths = {
    verified: (report.verified / total) * 100,
    warning: (report.warnings / total) * 100,
    error: (report.errors / total) * 100,
    unverifiable: (report.unverifiable / total) * 100,
  };

  function handleSort(col: SortCol) {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
  }

  function arrow(col: SortCol) {
    if (sortCol !== col) return <span className={styles.arrow}>&#9650;&#9660;</span>;
    return <span className={`${styles.arrow} ${styles.arrowActive}`}>{sortDir === "asc" ? "\u25BC" : "\u25B2"}</span>;
  }

  function handleDownloadPdf() {
    const link = document.createElement("a");
    link.href = `/api/reports/${report.id}/pdf`;
    link.download = "";
    link.click();
  }

  function handleDownloadExcel() {
    const link = document.createElement("a");
    link.href = `/api/reports/${report.id}/excel`;
    link.download = "";
    link.click();
  }

  function handlePrint() {
    window.print();
  }

  return (
    <div className={styles.wrapper}>
      {/* Navy Header */}
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <h1 className={styles.headerTitle}>CiteVerify &mdash; Citation Verification Report</h1>
          <div className={styles.headerSubtitle}>
            Document: {report.filename} &nbsp;|&nbsp; Generated: {createdDate}
          </div>
        </div>
        <div className={styles.headerButtons}>
          <button className={styles.headerBtn} onClick={handlePrint}>Print / PDF</button>
          <button className={styles.headerBtn} onClick={handleDownloadPdf}>Download PDF</button>
          <button className={styles.headerBtn} onClick={handleDownloadExcel}>Export Excel</button>
          <button className={styles.headerBtn} onClick={onReset}>New Document</button>
        </div>
      </div>

      <div className={styles.content}>
        <div className={styles.container}>

          {/* Color Bar */}
          <div className={styles.colorBar}>
            {barWidths.verified > 0 && <div className={styles.segVerified} style={{ width: `${barWidths.verified}%` }} />}
            {barWidths.warning > 0 && <div className={styles.segWarning} style={{ width: `${barWidths.warning}%` }} />}
            {barWidths.error > 0 && <div className={styles.segError} style={{ width: `${barWidths.error}%` }} />}
            {barWidths.unverifiable > 0 && <div className={styles.segUnverif} style={{ width: `${barWidths.unverifiable}%` }} />}
          </div>

          {/* Stats Row */}
          <div className={styles.statsRow}>
            <div className={styles.stat}><b>{report.total_citations}</b> Total</div>
            <div className={`${styles.stat} ${styles.statVerified}`}><b>{report.verified}</b> Verified</div>
            <div className={`${styles.stat} ${styles.statWarning}`}><b>{report.warnings}</b> Warnings</div>
            <div className={`${styles.stat} ${styles.statError}`}><b>{report.errors}</b> Errors</div>
            <div className={styles.stat}><b>{report.unverifiable}</b> Unverifiable</div>
          </div>

          {/* Summary */}
          <div className={styles.summary}>
            <p>CiteVerify analyzed <b>{report.total_citations} citations</b> in <b>{report.filename}</b>.</p>
            <ul>
              {report.errors > 0 && (
                <li><span className={styles.colorRed}>{report.errors}</span> contain errors that should be corrected before filing</li>
              )}
              {report.warnings > 0 && (
                <li><span className={styles.colorYellow}>{report.warnings}</span> have potential issues requiring review</li>
              )}
              {report.verified > 0 && (
                <li><span className={styles.colorGreen}>{report.verified}</span> were verified as accurate.</li>
              )}
            </ul>
            {errorCases.length > 0 && (
              <>
                <p className={styles.errorList}>The errors involve citations whose source opinions could not be matched to the cited cases:</p>
                <ul className={styles.errorCases}>
                  {errorCases.map((ec, i) => (
                    <li key={i}>{ec.name} ({ec.cite})</li>
                  ))}
                </ul>
              </>
            )}
            {flagged.length > 0 && <p>Review all flagged citations below.</p>}
          </div>

          {/* Download Buttons */}
          <div className={styles.btnRow}>
            <button className={styles.downloadBtn} onClick={handleDownloadPdf}>Download PDF</button>
            <button className={styles.exportBtn} onClick={handleDownloadExcel}>Export to Excel</button>
          </div>

          {/* Citation Summary Table */}
          {report.citations.length > 0 && (
            <>
              <h2 className={styles.sectionHead}>Citation Summary</h2>
              <div className={styles.tableWrapper}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th style={{ width: 36 }} onClick={() => handleSort("num")} className={sortCol === "num" ? styles.sortActive : ""}>
                        # {arrow("num")}
                      </th>
                      <th style={{ width: 210 }} onClick={() => handleSort("case")} className={styles.pairDivider}>
                        Case Name {arrow("case")}
                      </th>
                      <th style={{ width: 56 }} onClick={() => handleSort("caseV")} className={styles.pairDivider}>
                        Verified {arrow("caseV")}
                      </th>
                      <th style={{ width: 110 }} className={styles.groupDivider}>Citation</th>
                      <th style={{ width: 56 }} onClick={() => handleSort("citeV")} className={styles.pairDivider}>
                        Verified {arrow("citeV")}
                      </th>
                      <th style={{ width: 170 }} className={styles.groupDivider}>Quote</th>
                      <th style={{ width: 56 }} onClick={() => handleSort("quoteV")} className={styles.pairDivider}>
                        Verified {arrow("quoteV")}
                      </th>
                      <th style={{ width: 170 }} className={styles.groupDivider}>Characterization</th>
                      <th style={{ width: 56 }} onClick={() => handleSort("charV")} className={styles.pairDivider}>
                        Verified {arrow("charV")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((c) => (
                      <tr key={c.num} className={rowClass(c.verification.status)}>
                        <td className={styles.center}>{c.num}</td>
                        <td className={`${styles.caseName} ${styles.pairDividerTd}`}>{truncCase(c.extraction.case_name)}</td>
                        <td className={`${styles.center} ${styles.pairDividerTd}`}><CheckIcon value={caseVerified(c)} /></td>
                        <td className={styles.groupDividerTd}>{c.extraction.citation_text}</td>
                        <td className={`${styles.center} ${styles.pairDividerTd}`}><CheckIcon value={citeVerified(c)} /></td>
                        <td className={`${styles.quoteCol} ${styles.groupDividerTd}`}>
                          {c.extraction.quoted_text ? <em>&ldquo;{c.extraction.quoted_text}&rdquo;</em> : <span className={styles.gray}>&mdash;</span>}
                        </td>
                        <td className={`${styles.center} ${styles.pairDividerTd}`}><CheckIcon value={quoteVerified(c)} /></td>
                        <td className={`${styles.charCol} ${styles.groupDividerTd}`}>
                          {c.extraction.characterization || <span className={styles.gray}>&mdash;</span>}
                        </td>
                        <td className={`${styles.center} ${styles.pairDividerTd}`}><CheckIcon value={charVerified(c)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {/* Flagged Citations */}
          {flagged.length > 0 && (
            <>
              <h2 className={styles.sectionHead}>Flagged Citations</h2>
              <div>
                {flagged.map((c) => (
                  <div key={c.num} className={styles.flaggedItem}>
                    <div className={styles.flaggedHeader}>
                      <span className={styles.flaggedNum}>#{c.num}</span>{" "}
                      <span className={styles.flaggedCase}>{c.extraction.case_name}</span>{" "}
                      <span className={styles.flaggedCite}>&mdash; {c.extraction.citation_text}</span>{" "}
                      <span className={statusBadgeClass(c.verification.status)}>
                        {c.verification.status.toUpperCase()}
                      </span>
                    </div>

                    {c.extraction.quoted_text && (
                      <>
                        <div className={styles.flaggedField}>
                          <strong>Quote:</strong> &ldquo;{c.extraction.quoted_text}&rdquo;
                        </div>
                        <div className={styles.flaggedSub}>
                          Quote accuracy: {c.verification.quote_accuracy || "N/A"}
                          {c.verification.quote_diff && ` — Diff: ${c.verification.quote_diff}`}
                        </div>
                      </>
                    )}

                    {c.extraction.characterization && (
                      <>
                        <div className={styles.flaggedField}>
                          <strong>Characterization:</strong> {c.extraction.characterization}
                        </div>
                        <div className={styles.flaggedSub}>
                          Characterization accuracy: {c.verification.characterization_accuracy || "N/A"}
                        </div>
                      </>
                    )}

                    {c.verification.confidence > 0 && (
                      <div className={styles.flaggedSub}>
                        Confidence: {confidencePercent(c.verification.confidence)}
                      </div>
                    )}

                    {c.verification.reasoning && (
                      <div className={styles.flaggedReasoning}>{c.verification.reasoning}</div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {report.citations.length === 0 && (
            <div className={styles.empty}>No citations found in this document.</div>
          )}
        </div>

        {/* Footer */}
        <div className={styles.footer}>
          Generated by CiteVerify &mdash; AI-powered legal citation verification
        </div>
      </div>
    </div>
  );
}
