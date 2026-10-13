import { useRef, useState } from 'react';
import { ArrowUpRight, Check, Clipboard, Cloud, File, FileText, Image, LoaderCircle, LockKeyhole, UploadCloud, X } from 'lucide-react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL.replace(/\/$/, '');
const MAX_FILE_BYTES = 50 * 1024 * 1024;

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function FileIcon({ file }) {
  if (file?.type?.startsWith('image/')) return <Image size={22} />;
  if (file?.type?.startsWith('text/')) return <FileText size={22} />;
  return <File size={22} />;
}

export default function App() {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState('idle');
  const [shortUrl, setShortUrl] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  function chooseFile(candidate) {
    if (!candidate) return;
    setShortUrl('');
    setCopied(false);
    if (candidate.size > MAX_FILE_BYTES) {
      setError('This file is larger than 50 MB. Choose a smaller file.');
      setFile(null);
      setStatus('error');
      return;
    }
    setError('');
    setFile(candidate);
    setStatus('idle');
  }

  async function uploadFile() {
    if (!file || status === 'uploading') return;
    setStatus('uploading');
    setError('');
    const formData = new FormData();
    formData.append('file', file);
    try {
      const response = await fetch(`${API_BASE_URL}/api/files`, { method: 'POST', body: formData });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || `Upload failed (${response.status}).`);
      if (!result.shortUrl) throw new Error('The server response did not include a share URL.');
      setShortUrl(result.shortUrl);
      setStatus('success');
    } catch (uploadError) {
      setError(uploadError.message || 'Could not upload the file. Please try again.');
      setStatus('error');
    }
  }

  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(shortUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError('Could not copy automatically. Select and copy the URL instead.');
    }
  }

  function reset() {
    setFile(null);
    setStatus('idle');
    setShortUrl('');
    setError('');
    if (inputRef.current) inputRef.current.value = '';
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="CloudDrop home">
          <span className="brand-mark"><Cloud size={21} strokeWidth={2.4} /></span>
          <span>cloud<span className="brand-light">drop</span></span>
        </a>
        <div className="secure-note"><LockKeyhole size={14} /> Simple, secure sharing</div>
      </header>

      <section className="hero" id="top">
        <div className="eyebrow"><span className="eyebrow-dot" /> FILE SHARING, MADE SIMPLE</div>
        <h1>Drop it here.<br /><span>Share it anywhere.</span></h1>
        <p className="intro">Upload a file and get a link you can send to anyone.<br className="desktop-break" /> No extra steps, just a simple way to share.</p>

        <section
          className={`upload-card ${dragging ? 'is-dragging' : ''} ${status === 'success' ? 'is-success' : ''}`}
          onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
          onDrop={(event) => { event.preventDefault(); setDragging(false); chooseFile(event.dataTransfer.files?.[0]); }}
        >
          {status === 'success' ? (
            <div className="result-view">
              <div className="success-icon"><Check size={25} strokeWidth={2.7} /></div>
              <div className="result-heading">Your file is ready to share</div>
              <p className="result-subtitle">Anyone with this link can download your file.</p>
              <div className="url-row">
                <a className="url-value" href={shortUrl} target="_blank" rel="noreferrer">{shortUrl}</a>
                <button className="copy-button" onClick={copyUrl} aria-label="Copy share link">{copied ? <Check size={17} /> : <Clipboard size={17} />}<span>{copied ? 'Copied' : 'Copy link'}</span></button>
              </div>
              <button className="another-button" onClick={reset}>Upload another file <ArrowUpRight size={15} /></button>
            </div>
          ) : (
            <>
              {!file ? (
                <div className="empty-state">
                  <div className="upload-icon"><UploadCloud size={28} strokeWidth={1.8} /></div>
                  <h2>Choose a file to share</h2>
                  <p>Drag and drop your file here, or browse your device</p>
                  <button className="browse-button" onClick={() => inputRef.current?.click()}>Browse files <ArrowUpRight size={16} /></button>
                  <input ref={inputRef} type="file" hidden onChange={(event) => chooseFile(event.target.files?.[0])} />
                  <div className="file-limit">Maximum file size <span>50 MB</span></div>
                </div>
              ) : (
                <div className="selected-state">
                  <div className="selected-topline"><div className="upload-icon small"><FileIcon file={file} /></div><span className="ready-label">READY TO UPLOAD</span><button className="remove-button" onClick={reset} aria-label="Remove selected file" disabled={status === 'uploading'}><X size={18} /></button></div>
                  <div className="file-name" title={file.name}>{file.name}</div>
                  <div className="file-size">{formatSize(file.size)}{file.type ? ` · ${file.type}` : ''}</div>
                  <button className="upload-button" onClick={uploadFile} disabled={status === 'uploading'}>
                    {status === 'uploading' ? <><LoaderCircle className="spinner" size={18} /> Uploading…</> : <>Upload file <ArrowUpRight size={17} /></>}
                  </button>
                  {status === 'error' && <div className="error-message" role="alert">{error}</div>}
                </div>
              )}
            </>
          )}
        </section>
        {error && status === 'error' && !file && <div className="error-message outside-error" role="alert">{error}</div>}
        <div className="privacy-note"><LockKeyhole size={14} /> Your file is stored securely in the cloud.</div>
      </section>

      <footer className="footer"><span>CloudDrop</span><span className="footer-separator">·</span><span>Share something good.</span></footer>
    </main>
  );
}
