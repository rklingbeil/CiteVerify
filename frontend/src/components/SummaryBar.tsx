import type { VerificationReport } from "../types";
import styles from "./SummaryBar.module.css";

interface Props {
  report: VerificationReport;
  onReset: () => void;
}

export default function SummaryBar({ report, onReset }: Props) {
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

  return (
    <div className={styles.bar}>
      <div className={styles.left}>
        <span className={styles.filename}>{report.filename}</span>
        <span className={styles.total}>
          {report.total_citations} citation{report.total_citations !== 1 ? "s" : ""}
        </span>
      </div>

      <div className={styles.badges}>
        {report.verified > 0 && (
          <span className={`${styles.badge} ${styles.verified}`}>
            {report.verified} verified
          </span>
        )}
        {report.warnings > 0 && (
          <span className={`${styles.badge} ${styles.warning}`}>
            {report.warnings} warning{report.warnings !== 1 ? "s" : ""}
          </span>
        )}
        {report.errors > 0 && (
          <span className={`${styles.badge} ${styles.error}`}>
            {report.errors} error{report.errors !== 1 ? "s" : ""}
          </span>
        )}
        {report.unverifiable > 0 && (
          <span className={`${styles.badge} ${styles.unverifiable}`}>
            {report.unverifiable} unverifiable
          </span>
        )}
      </div>

      <div className={styles.actions}>
        <button className={styles.exportBtn} onClick={handleDownloadPdf} title="Download PDF report">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M4 12h8M8 2v8m0 0L5 7m3 3l3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          PDF
        </button>
        <button className={styles.exportBtn} onClick={handleDownloadExcel} title="Download Excel spreadsheet">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M4 12h8M8 2v8m0 0L5 7m3 3l3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Excel
        </button>
        <button className={styles.newBtn} onClick={onReset}>
          New Document
        </button>
      </div>
    </div>
  );
}
