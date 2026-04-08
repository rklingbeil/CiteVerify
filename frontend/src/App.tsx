import { useState } from "react";
import type { JobStatus, VerificationReport } from "./types";
import UploadPanel from "./components/UploadPanel";
import ProgressPanel from "./components/ProgressPanel";
import ReportView from "./components/ReportView";
import styles from "./App.module.css";

type AppState = "upload" | "processing" | "report";

export default function App() {
  const [state, setState] = useState<AppState>("upload");
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [report, setReport] = useState<VerificationReport | null>(null);

  function handleUploadSuccess(newJobId: string) {
    setJobId(newJobId);
    setState("processing");
  }

  function handleJobComplete(status: JobStatus, completedReport: VerificationReport) {
    setJobStatus(status);
    setReport(completedReport);
    setState("report");
  }

  function handleJobFailed(status: JobStatus) {
    setJobStatus(status);
  }

  function handleReset() {
    setState("upload");
    setJobId(null);
    setJobStatus(null);
    setReport(null);
  }

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <h1 className={styles.logo} onClick={handleReset}>
          <span className={styles.logoIcon}>✓</span>
          CiteVerify
        </h1>
        <span className={styles.tagline}>Legal Citation Verification</span>
      </header>

      <main className={styles.main}>
        {state === "upload" && (
          <UploadPanel onSuccess={handleUploadSuccess} />
        )}

        {state === "processing" && jobId && (
          <ProgressPanel
            jobId={jobId}
            onComplete={handleJobComplete}
            onFailed={handleJobFailed}
            onReset={handleReset}
            failedStatus={jobStatus?.status === "failed" ? jobStatus : null}
          />
        )}

        {state === "report" && report && (
          <ReportView report={report} onReset={handleReset} />
        )}
      </main>
    </div>
  );
}
