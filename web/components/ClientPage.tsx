"use client";

import { useEffect, useState } from "react";
import { getWebinars, getResults } from "@/lib/api";
import type { Webinar, WebinarResults } from "@/lib/api";
import HomePage from "./HomePage";

export default function ClientPage() {
  const [webinars, setWebinars] = useState<Webinar[]>([]);
  const [featured, setFeatured] = useState<WebinarResults | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    Promise.all([getWebinars(), getResults("custom-ai-signals")]).then(
      ([w, f]) => {
        setWebinars(w);
        setFeatured(f);
        setReady(true);
      }
    );
  }, []);

  if (!ready) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-[#F15A24] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return <HomePage webinars={webinars} featured={featured} />;
}
