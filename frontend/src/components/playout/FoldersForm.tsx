/**
 * The local folders, one row each: path (typed, or picked with "browse",
 * which lists the streaming PC's own drives and folders), label (the
 * library group), recursive, and "this is the receiver root" (one radio for
 * all rows: delivered clips land there, and its files without a manifest
 * entry are "unmatched").
 */

import { useEffect, useState } from "react";

import {
  browseFolders,
  newFolder,
  withReceiverRoot,
  type FolderListing,
  type LocalFolderValues,
} from "../../lib/playoutSettings";

interface FoldersFormProps {
  folders: LocalFolderValues[];
  onChange: (folders: LocalFolderValues[]) => void;
}

export function FoldersForm({ folders, onChange }: FoldersFormProps) {
  const [browsingRow, setBrowsingRow] = useState<number | null>(null);
  const change = (index: number, patch: Partial<LocalFolderValues>) =>
    onChange(folders.map((folder, row) => (row === index ? { ...folder, ...patch } : folder)));
  return (
    <div className="folders-form">
      {folders.length === 0 && (
        <p className="muted">No folders yet: add the receiver&apos;s clip root.</p>
      )}
      {folders.map((folder, index) => (
        <div className="folder-row" key={index} data-testid={`folder-${index}`}>
          <input
            aria-label={`Folder ${index + 1} path`}
            placeholder="D:\clips"
            value={folder.path}
            onChange={(event) => change(index, { path: event.target.value })}
          />
          <button
            type="button"
            onClick={() => setBrowsingRow(browsingRow === index ? null : index)}
          >
            browse
          </button>
          <input
            aria-label={`Folder ${index + 1} label`}
            placeholder="Label (library group)"
            value={folder.label}
            onChange={(event) => change(index, { label: event.target.value })}
          />
          <label className="inline">
            <input
              type="checkbox"
              checked={folder.recursive}
              onChange={(event) => change(index, { recursive: event.target.checked })}
            />
            subfolders
          </label>
          <label className="inline">
            <input
              type="radio"
              name="receiver-root"
              aria-label={`Folder ${index + 1} is the receiver root`}
              checked={folder.receiver_root}
              onChange={() => onChange(withReceiverRoot(folders, index))}
            />
            receiver root
          </label>
          <button
            type="button"
            aria-label={`Remove folder ${index + 1}`}
            onClick={() => onChange(folders.filter((_, row) => row !== index))}
          >
            ✕
          </button>
          {browsingRow === index && (
            <FolderBrowser
              start={folder.path}
              onPick={(path) => {
                change(index, { path });
                setBrowsingRow(null);
              }}
            />
          )}
        </div>
      ))}
      <button type="button" onClick={() => onChange([...folders, newFolder(folders.length === 0)])}>
        Add folder
      </button>
    </div>
  );
}

/** Walks the streaming PC's folders; "use this folder" picks the one shown. */
function FolderBrowser({ start, onPick }: { start: string; onPick: (path: string) => void }) {
  const [path, open] = useState(start);
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    browseFolders(path)
      .then((result) => {
        if (!cancelled) {
          setListing(result);
          setError(null);
        }
      })
      .catch((failure: Error) => {
        if (!cancelled) setError(failure.message);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);
  if (listing === null && error === null) {
    return <div className="folder-browser muted">Loading…</div>;
  }
  return (
    <div className="folder-browser" data-testid="folder-browser">
      {error && <div className="field-error">{error}</div>}
      {listing && (
        <>
          <div className="mono">{listing.path || "This PC"}</div>
          {listing.error && <div className="field-error">{listing.error}</div>}
          <ul>
            {listing.parent !== null && (
              <li>
                <button type="button" onClick={() => open(listing.parent ?? "")}>
                  ↑ up
                </button>
              </li>
            )}
            {listing.folders.map((folder) => (
              <li key={folder.path}>
                <button type="button" onClick={() => open(folder.path)}>
                  📁 {folder.name}
                </button>
              </li>
            ))}
          </ul>
          {listing.path && (
            <button type="button" onClick={() => onPick(listing.path)}>
              Use this folder
            </button>
          )}
        </>
      )}
    </div>
  );
}
