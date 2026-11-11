const STEPS = [
  {
    n: "01",
    label: "Transcribe",
    sub: "Transcribes every word and knows who said what.",
  },
  {
    n: "02",
    label: "Find moments",
    sub: "Claude scans for hooks: stats, stories, contrarians, frameworks",
  },
  {
    n: "03",
    label: "Score vs real posts",
    sub: "Each moment scored against 861 real GTM LinkedIn posts",
  },
  {
    n: "04",
    label: "Write content",
    sub: "LinkedIn post, carousel, Unify University lesson, clip caption",
  },
  {
    n: "05",
    label: "Ship",
    sub: "Ranked results ready to review and publish",
  },
];

export default function HowItWorks() {
  return (
    <section className="py-24 px-6 max-w-6xl mx-auto">
      <h2
        className="text-3xl md:text-4xl mb-3 text-[#1A1A1A]"
        style={{ fontFamily: "var(--font-serif)" }}
      >
        How it works
      </h2>
      <p className="text-[#6B6560] text-sm mb-12 max-w-xl">
        Five steps from raw video to LinkedIn-ready clips, fully automated.
      </p>

      {/* Steps */}
      <div className="relative">
        {/* Connecting line (desktop) */}
        <div
          className="hidden md:block absolute top-7 left-0 right-0 h-px bg-[#E8E4DC]"
          style={{ zIndex: 0 }}
        />
        <div className="grid grid-cols-1 md:grid-cols-5 gap-8 md:gap-4 relative">
          {STEPS.map((step) => (
            <div key={step.n} className="flex flex-col items-start md:items-center text-center md:text-center relative">
              {/* Step circle */}
              <div
                className="w-14 h-14 rounded-full border border-[#E8E4DC] bg-[#FFFDF6] flex items-center justify-center mb-3 relative z-10"
                style={{ fontFamily: "var(--font-serif)" }}
              >
                <span className="text-sm text-[#F15A24] font-medium">
                  {step.n}
                </span>
              </div>
              <p className="font-semibold text-[#1A1A1A] text-sm mb-1">
                {step.label}
              </p>
              <p className="text-xs text-[#6B6560] leading-snug">{step.sub}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Honest note */}
      <div className="mt-16 rounded-xl border border-[#E8E4DC] bg-white px-6 py-5 max-w-2xl">
        <p className="text-xs uppercase tracking-wider text-[#6B6560] mb-2 font-medium">
          Honest caveat
        </p>
        <p className="text-sm text-[#1A1A1A] leading-relaxed">
          Backtested on 168 real posts. On Austin&apos;s posts, the ranking
          tracks real performance (ρ ≈ 0.5). v2 learns from Unify&apos;s own
          posted results every week.
        </p>
      </div>
    </section>
  );
}
