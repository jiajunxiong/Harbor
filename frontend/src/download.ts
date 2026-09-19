/**
 * Browser file saving (MVP 5 / SP 5.22).
 *
 * The report document is rendered by the server; the browser's only job is to
 * put the received bytes on disk under the name the server chose. Kept out of the
 * component so the component stays declarative and this can be tested on its own.
 */

/**
 * Save a blob under a filename, cleaning up the object URL afterwards.
 *
 * Revoking matters: an object URL pins the blob in memory for the lifetime of
 * the document, so a reader downloading three reports would keep all three until
 * they navigated away.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * The filename to use for a download.
 *
 * The server's `Content-Disposition` name wins when it could be read (same-origin
 * requests can read it; a cross-origin one cannot), and the caller's fallback is
 * used otherwise — a download without a name would land as "download".
 */
export function downloadFilename(suggested: string | null, fallback: string): string {
  const name = suggested?.trim();
  return name !== undefined && name !== "" ? name : fallback;
}
