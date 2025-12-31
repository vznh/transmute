"use client";

import { useState } from "react";

interface Result {
  id: string;
  status: "success" | "error";
  b64: string | null;
  error: string | null;
  metadata: { title: string | null; uploader: string | null; duration: number | null } | null;
}

interface InputItem {
  id: string;
  url: string;
}

const MAX_URLS = 10;

export default function Home() {
  const [inputs, setInputs] = useState<InputItem[]>([
    { id: "1", url: "" },
    { id: "2", url: "" },
  ]);
  const [results, setResults] = useState<Result[]>([]);
  const [converting, setConverting] = useState(false);
  const [pasteBuffer, setPasteBuffer] = useState("");

  const updateUrl = (id: string, val: string) => {
    setInputs((prev) => prev.map((i) => (i.id === id ? { ...i, url: val } : i)));
  };

  const addInput = () => {
    if (inputs.length < MAX_URLS) {
      setInputs((prev) => [...prev, { id: String(Date.now()), url: "" }]);
    }
  };

  const removeInput = (id: string) => {
    setInputs((prev) => prev.filter((i) => i.id !== id));
  };

  const handlePaste = () => {
    // Split by https:// to extract multiple URLs
    const parts = pasteBuffer.split("https://").filter((p) => p.trim());
    const urls = parts.map((p) => `https://${p.trim()}`);

    const newInputs: InputItem[] = [];
    const existing = [...inputs];

    for (const url of urls) {
      if (newInputs.length >= MAX_URLS) break;
      const slot = existing.find((i) => !i.url);
      if (slot) {
        slot.url = url;
        existing.splice(existing.indexOf(slot), 1);
      } else if (newInputs.length < MAX_URLS) {
        newInputs.push({ id: String(Date.now() + newInputs.length), url });
      }
    }

    setInputs([...existing, ...newInputs]);
    setPasteBuffer("");
  };

  const convert = async () => {
    const urls = inputs.map((i) => i.url).filter((u) => u.trim());

    if (urls.length === 0) return;

    setConverting(true);
    setResults([]);

    try {
      const res = await fetch("/api/convert", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ urls }),
      });

      const data = await res.json();
      setResults(data);
    } catch (e) {
      alert("Failed to convert: " + (e instanceof Error ? e.message : "Unknown error"));
    } finally {
      setConverting(false);
    }
  };

  const downloadOne = (r: Result) => {
    if (!r.b64) return;
    const bin = atob(r.b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const blob = new Blob([bytes], { type: "audio/mpeg" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${r.metadata?.uploader || "unknown"} - ${r.metadata?.title || "track"}.mp3`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadAll = () => {
    const ok = results.filter((r) => r.status === "success" && r.b64);
    ok.forEach((r) => setTimeout(() => downloadOne(r), 100 * results.indexOf(r)));
  };

  const fmtTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black text-zinc-900 dark:text-zinc-50 p-8">
      <div className="max-w-2xl mx-auto">
        <h1 className="text-3xl font-bold mb-2">SC/YT to MP3</h1>
        <p className="text-zinc-500 mb-6">Convert SoundCloud & YouTube to MP3 (max 10)</p>

        {/* URL Inputs */}
        <div className="space-y-2 mb-4">
          {inputs.map((item, idx) => (
            <div key={item.id} className="flex gap-2">
              <span className="w-6 h-10 flex items-center justify-center text-zinc-400 text-sm">
                {idx + 1}
              </span>
              <input
                type="text"
                value={item.url}
                onChange={(e) => updateUrl(item.id, e.target.value)}
                placeholder="https://soundcloud.com/... or https://youtube.com/..."
                className="flex-1 px-3 py-2 rounded bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              {inputs.length > 1 && (
                <button
                  onClick={() => removeInput(item.id)}
                  className="px-3 text-zinc-400 hover:text-red-500"
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>

        {/* Add more button */}
        {inputs.length < MAX_URLS && (
          <button
            onClick={addInput}
            className="text-sm text-blue-500 hover:underline mb-4"
          >
            + Add more
          </button>
        )}

        {/* Paste buffer */}
        <div className="border-t border-zinc-200 dark:border-zinc-800 pt-4 mb-4">
          <p className="text-sm text-zinc-500 mb-2">
            Paste multiple URLs separated by "https://":
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              value={pasteBuffer}
              onChange={(e) => setPasteBuffer(e.target.value)}
              placeholder="https://sc.com/track1https://sc.com/track2..."
              className="flex-1 px-3 py-2 rounded bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 text-sm"
            />
            <button
              onClick={handlePaste}
              className="px-4 py-2 bg-zinc-200 dark:bg-zinc-800 rounded hover:bg-zinc-300 dark:hover:bg-zinc-700"
            >
              Parse
            </button>
          </div>
        </div>

        {/* Convert button */}
        <button
          onClick={convert}
          disabled={converting}
          className="w-full py-3 bg-blue-500 hover:bg-blue-600 disabled:bg-zinc-300 dark:disabled:bg-zinc-800 rounded font-medium transition-colors"
        >
          {converting ? "Converting..." : "Convert"}
        </button>

        {/* Results */}
        {results.length > 0 && (
          <div className="mt-8 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-semibold">Results</h2>
              {results.some((r) => r.status === "success") && (
                <button
                  onClick={downloadAll}
                  className="text-sm text-blue-500 hover:underline"
                >
                  Download All
                </button>
              )}
            </div>

            {results.map((r) => (
              <div
                key={r.id}
                className={`p-4 rounded border ${
                  r.status === "success"
                    ? "bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-800"
                    : "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
                }`}
              >
                {r.status === "success" ? (
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium truncate">{r.metadata?.title}</p>
                      <p className="text-sm text-zinc-500">
                        {r.metadata?.uploader} • {r.metadata?.duration ? fmtTime(r.metadata.duration) : "Unknown"}
                      </p>
                    </div>
                    <button
                      onClick={() => downloadOne(r)}
                      className="px-3 py-1 bg-blue-500 hover:bg-blue-600 rounded text-sm"
                    >
                      Download
                    </button>
                  </div>
                ) : (
                  <div>
                    <p className="text-red-500 font-medium">Failed</p>
                    <p className="text-sm text-red-400 mt-1">{r.error}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
