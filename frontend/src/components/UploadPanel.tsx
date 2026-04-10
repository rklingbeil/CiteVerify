import { useCallback, useRef, useState } from "react";
import client from "../api/client";
import type { UploadResponse } from "../types";
import styles from "./UploadPanel.module.css";

interface Props {
  onSuccess: (jobId: string) => void;
}

export default function UploadPanel({ onSuccess }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = ".pdf,.docx";

  function validateFile(f: File): string | null {
    const ext = f.name.split(".").pop()?.toLowerCase();
    if (ext !== "pdf" && ext !== "docx") {
      return "Unsupported file type. Upload PDF or DOCX.";
    }
    if (f.size > 50 * 1024 * 1024) {
      return "File too large. Maximum 50 MB.";
    }
    return null;
  }

  function handleFileSelect(f: File) {
    const err = validateFile(f);
    if (err) {
      setError(err);
      setFile(null);
      return;
    }
    setError(null);
    setFile(f);
  }

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) handleFileSelect(dropped);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOver(false);
  }, []);

  function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0];
    if (selected) handleFileSelect(selected);
  }

  async function handleVerify() {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await client.post<UploadResponse>("/upload", form);
      onSuccess(resp.data.job_id);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } }).response?.data?.detail ||
        "Upload failed. Please try again.";
      setError(msg);
    } finally {
      setUploading(false);
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  return (
    <div className={styles.container}>
      <div className={styles.card}>
        <h2 className={styles.title}>Verify Legal Citations</h2>
        <p className={styles.subtitle}>
          Upload a legal document to verify all citations, quotes, and case characterizations
          against source opinions.
        </p>

        <div
          className={`${styles.dropzone} ${dragOver ? styles.dropzoneActive : ""} ${file ? styles.dropzoneHasFile : ""}`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onClick={() => inputRef.current?.click()}
        >
          <input
            ref={inputRef}
            type="file"
            accept={accept}
            onChange={handleInputChange}
            className={styles.hiddenInput}
          />

          {file ? (
            <div className={styles.fileInfo}>
              <div className={styles.fileIcon}>
                {file.name.endsWith(".pdf") ? "PDF" : "DOC"}
              </div>
              <div className={styles.fileMeta}>
                <span className={styles.fileName}>{file.name}</span>
                <span className={styles.fileSize}>{formatSize(file.size)}</span>
              </div>
              <button
                className={styles.removeBtn}
                aria-label="Remove file"
                onClick={(e) => {
                  e.stopPropagation();
                  setFile(null);
                  setError(null);
                  if (inputRef.current) inputRef.current.value = "";
                }}
              >
                ×
              </button>
            </div>
          ) : (
            <div className={styles.dropPrompt}>
              <div className={styles.uploadIcon}>↑</div>
              <p>Drop a PDF or DOCX here</p>
              <p className={styles.dropSubtext}>or click to browse</p>
            </div>
          )}
        </div>

        {error && <p className={styles.error}>{error}</p>}

        <button
          className={styles.verifyBtn}
          disabled={!file || uploading}
          onClick={handleVerify}
        >
          {uploading ? "Uploading..." : "Verify Citations"}
        </button>

        <div className={styles.steps}>
          <div className={styles.step}>
            <span className={styles.stepNum}>1</span>
            <span>Extract citations & quotes</span>
          </div>
          <div className={styles.step}>
            <span className={styles.stepNum}>2</span>
            <span>Look up source cases</span>
          </div>
          <div className={styles.step}>
            <span className={styles.stepNum}>3</span>
            <span>Verify accuracy with AI</span>
          </div>
        </div>
      </div>
    </div>
  );
}
