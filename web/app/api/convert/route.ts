/**
 * API route that proxies to FastAPI backend.
 * POST /api/convert - Convert URLs to MP3
 * Uses dev environment by default (set NEXT_PUBLIC_MODAL_ENV to change)
 */
import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.FASTAPI_URL || "http://localhost:8000";
const MODAL_ENV = process.env.NEXT_PUBLIC_MODAL_ENV || "dev";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { urls } = body;

    if (!urls || !Array.isArray(urls) || urls.length === 0) {
      return NextResponse.json({ error: "No URLs provided" }, { status: 400 });
    }

    if (urls.length > 10) {
      return NextResponse.json({ error: "Max 10 URLs per request" }, { status: 400 });
    }

    const res = await fetch(`${API_URL}/convert`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Modal-Env": MODAL_ENV,
      },
      body: JSON.stringify({ urls }),
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : "Failed to convert" },
      { status: 500 }
    );
  }
}
