import type { VerificationReport } from "../types";
import styles from "./SummaryBar.module.css";

interface Props {
  report: VerificationReport;
  onReset: () => void;
}

export default function SummaryBar({ report, onReset }: Props) {
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

      <button className={styles.newBtn} onClick={onReset}>
        New Document
      </button>
    </div>
  );
}
