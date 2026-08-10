"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { AvatarUpload } from "@/components/AvatarUpload";
import { AvatarList } from "@/components/AvatarList";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { AuthModal } from "@/components/AuthModal";
import { api } from "@/lib/api";
import { toast } from "react-hot-toast";
import { useStore } from "@/store/useStore";
import { StudioPanel } from "@/components/StudioPanel";

// Heavy panels load on-demand to keep the initial bundle small.
const VoicePanel = dynamic(() => import("@/components/VoicePanel").then((m) => m.VoicePanel), {
  ssr: false,
  loading: () => <PanelLoader label="Loading voice studio…" />,
});

const ExplainerPanel = dynamic(() => import("@/components/ExplainerPanel").then((m) => m.ExplainerPanel), {
  ssr: false,
  loading: () => <PanelLoader label="Loading video studio…" />,
});

const TutorialPanel = dynamic(() => import("@/components/TutorialPanel").then((m) => m.TutorialPanel), {
  ssr: false,
  loading: () => <PanelLoader label="Loading guides…" />,
});

function PanelLoader({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-20 text-[#86868B] text-sm">
      <span className="inline-block w-2 h-2 rounded-full bg-[#0071E3] animate-pulse mr-2" />
      {label}
    </div>
  );
}

import {
  Camera,
  Mic2,
  Sparkles,
  Zap,
  Globe,
  Shield,
  Play,
  ChevronRight,
  Activity,
  Brain,
  AudioWaveform,
  Clapperboard,
  Film,
  BookOpen,
} from "lucide-react";

const FEATURES = [
  {
    icon: Shield,
    title: "Runs on your premises",
    description: "Deploy the entire stack inside your own network. Nothing leaves your servers — ever.",
  },
  {
    icon: Brain,
    title: "Open source, end to end",
    description: "Every model and service is open and self-hosted. Inspect it, extend it, own it — no black boxes.",
  },
  {
    icon: Zap,
    title: "No token costs",
    description: "No per-token API bills, no usage meters, no rate limits. Generate as much as your hardware allows.",
  },
  {
    icon: Globe,
    title: "No data sharing",
    description: "Your decks, voices, faces, and videos are never sent to a third party. Zero external calls.",
  },
  {
    icon: AudioWaveform,
    title: "Whole content-generation engine",
    description: "Transcription, scripting, voice cloning, and lip-synced avatar video — one integrated pipeline.",
  },
  {
    icon: Activity,
    title: "Enterprise-grade security",
    description: "Per-user isolation, JWT auth, and full audit of every job. Your data stays inside your walls.",
  },
];

const STATS = [
  { value: "100%", label: "On-premise" },
  { value: "$0", label: "Per-token cost" },
  { value: "Zero", label: "Data shared" },
  { value: "Open", label: "Source" },
];

type View = "home" | "avatars" | "voice" | "studio" | "explainer" | "tutorial";

export default function Home() {
  const { isAuthenticated, user, clearAuth } = useStore();
  const [selectedAvatar, setSelectedAvatar] = useState<string | null>(null);
  const [, setActiveSessionId] = useState<string | null>(null);
  const [, setResumeSessionId] = useState<string | null>(null);
  const [view, setView] = useState<View>("home");

  const handleVoiceSelect = async (voiceId: string) => {
    if (!selectedAvatar) {
      toast("Select an avatar first to assign this voice", { icon: "💡" });
      return;
    }
    try {
      await api.setAvatarVoice(selectedAvatar, voiceId);
      toast.success("Voice assigned to avatar", { icon: "🎙️" });
    } catch {
      toast.error("Failed to assign voice");
    }
  };

  const handleSelectAvatar = (id: string) => {
    setSelectedAvatar(id);
    setResumeSessionId(null);
  };

  // keep setter referenced so it isn't flagged unused
  void setActiveSessionId;

  const navItems: { id: View; icon: typeof Sparkles; label: string; disabled?: boolean }[] = [
    { id: "home", icon: Sparkles, label: "Home" },
    { id: "avatars", icon: Camera, label: "Avatars" },
    { id: "voice", icon: Mic2, label: "Voice" },
    { id: "studio", icon: Clapperboard, label: "API" },
    { id: "explainer", icon: Film, label: "Video" },
    { id: "tutorial", icon: BookOpen, label: "Guides" },
  ];

  return (
    <div className="min-h-screen" style={{ background: "#FBFBFD", color: "#1D1D1F" }}>
      {/* ── Auth gate ── */}
      {!isAuthenticated() && <AuthModal />}

      {/* ── Navigation ── */}
      <nav className="fixed top-0 left-0 right-0 z-50 h-16 bg-white/80 backdrop-blur-xl border-b border-[#E5E5EA]"
           style={{ WebkitBackdropFilter: "saturate(180%) blur(20px)", backdropFilter: "saturate(180%) blur(20px)" }}>
        <div className="h-full mx-auto max-w-7xl px-6 flex items-center justify-between">
          <button onClick={() => setView("home")} className="flex items-center gap-2.5">
            <img src="/chalchitra-logo.png" alt="Chalchitra" className="w-8 h-8 rounded-[9px] object-contain" />
            <span className="wordmark-chalchitra text-[23px] leading-none text-[#1D1D1F]">Chalchitra</span>
          </button>

          <div className="flex items-center gap-0.5 p-1 rounded-full bg-[#F5F5F7] border border-[#E5E5EA] overflow-x-auto">
            {navItems.map(({ id, icon: Icon, label, disabled }) => (
              <button
                key={id}
                onClick={() => !disabled && setView(id)}
                disabled={disabled || undefined}
                aria-current={view === id ? "page" : undefined}
                className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-[13px] font-medium transition-all duration-200 flex-shrink-0
                  ${
                    view === id
                      ? "bg-white text-[#1D1D1F] shadow-sm"
                      : "text-[#6E6E73] hover:text-[#1D1D1F] disabled:opacity-30 disabled:cursor-not-allowed"
                  }`}
              >
                <Icon size={14} />
                <span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2.5">
            <ThemeToggle />
            {user && (
              <div className="flex items-center gap-1.5 pl-1.5 pr-1 py-1 rounded-full bg-[#F5F5F7] border border-[#E5E5EA]">
                <div className="flex items-center gap-2 pl-1 pr-2">
                  <div className="w-7 h-7 rounded-full bg-[#0071E3] flex items-center justify-center text-white text-[13px] font-semibold flex-shrink-0">
                    {(user.username?.[0] || "U").toUpperCase()}
                  </div>
                  <span className="text-[13px] font-medium text-[#1D1D1F] hidden sm:block max-w-[140px] truncate">{user.username}</span>
                </div>
                <button
                  onClick={() => {
                    api.logout();
                    clearAuth();
                  }}
                  className="text-[12px] font-medium text-[#6E6E73] hover:text-[#D70015] transition-colors px-2.5 py-1.5 rounded-full hover:bg-white"
                  title="Sign out"
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </nav>

      <main className="pt-16">
        {/* ── HOME VIEW ── */}
        {view === "home" && (
          <div className="animate-fade-in">
            {/* Hero */}
            <section className="relative flex flex-col items-center justify-center min-h-[calc(100vh-4rem)] px-6 text-center">
              <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-[#0071E3]/[0.08] mb-8">
                <Sparkles size={13} className="text-[#0071E3]" />
                <span className="text-[13px] text-[#0071E3] font-semibold">Open-source · On-premise · No data leaves your network</span>
              </div>

              <h1 className="text-[56px] md:text-[76px] font-bold leading-[1.02] mb-6 tracking-[-0.03em] text-[#1D1D1F] max-w-4xl">
                The AI video engine
                <br />
                that runs on your servers.
              </h1>

              <p className="max-w-2xl text-[19px] md:text-[21px] text-[#6E6E73] mb-10 leading-relaxed">
                A fully open-source, on-premise content-generation engine. Turn decks into
                narrated avatar videos — transcription, scripting, voice cloning, and lip-sync —
                with no token costs, no data sharing, and nothing ever leaving your network.
              </p>

              <div className="flex flex-wrap items-center justify-center gap-3">
                <button
                  onClick={() => setView("explainer")}
                  className="inline-flex items-center gap-2 text-[16px] font-semibold px-7 py-3 rounded-full text-white bg-[#0071E3] hover:bg-[#0077ED] transition-all active:scale-[0.98] group"
                >
                  <Play size={17} fill="white" />
                  Open Video Studio
                  <ChevronRight size={16} className="group-hover:translate-x-0.5 transition-transform" />
                </button>
                <button
                  onClick={() => setView("voice")}
                  className="inline-flex items-center gap-2 text-[16px] font-semibold px-7 py-3 rounded-full text-[#0071E3] bg-white border border-[#D2D2D7] hover:bg-[#F5F5F7] transition-all active:scale-[0.98]"
                >
                  <Mic2 size={17} />
                  Clone a voice
                </button>
              </div>

              <div className="flex flex-wrap items-center justify-center gap-x-12 gap-y-6 mt-20">
                {STATS.map(({ value, label }) => (
                  <div key={label} className="text-center">
                    <div className="text-[34px] font-bold text-[#1D1D1F] tracking-[-0.02em]">{value}</div>
                    <div className="text-[14px] text-[#86868B] mt-0.5">{label}</div>
                  </div>
                ))}
              </div>
            </section>

            {/* Features */}
            <section className="px-6 pb-28 max-w-6xl mx-auto">
              <div className="text-center mb-16">
                <h2 className="text-[40px] font-bold mb-4 tracking-[-0.02em] text-[#1D1D1F]">
                  Own the whole pipeline.
                </h2>
                <p className="text-[#6E6E73] text-[19px] max-w-2xl mx-auto leading-relaxed">
                  A complete content-generation engine — open source, self-hosted, and private by design.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                {FEATURES.map(({ icon: Icon, title, description }) => (
                  <div
                    key={title}
                    className="bg-white rounded-[18px] border border-[#EDEDF0] p-7 transition-all duration-300 hover:-translate-y-0.5"
                    style={{ boxShadow: "0 4px 24px rgba(0,0,0,0.05)" }}
                  >
                    <div className="w-11 h-11 rounded-[12px] bg-[#0071E3]/[0.08] flex items-center justify-center mb-4">
                      <Icon size={20} className="text-[#0071E3]" />
                    </div>
                    <h3 className="font-semibold text-[17px] text-[#1D1D1F] mb-1.5">{title}</h3>
                    <p className="text-[#6E6E73] text-[14px] leading-relaxed">{description}</p>
                  </div>
                ))}
              </div>
            </section>

            {/* Footer */}
            <footer className="border-t border-[#E5E5EA] py-8">
              <div className="max-w-6xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-[#86868B]">
                  <img src="/chalchitra-logo.png" alt="Chalchitra" className="w-5 h-5 rounded-md object-contain" />
                  <span className="wordmark-chalchitra text-[16px] text-[#1D1D1F]">Chalchitra</span>
                </div>
                <p className="text-[13px] text-[#AEAEB2]">Open-source, on-premise AI video generation — your data never leaves your network</p>
              </div>
            </footer>
          </div>
        )}

        {/* ── AVATAR VIEW ── */}
        {view === "avatars" && (
          <div className="lightscope max-w-7xl mx-auto px-6 py-12 animate-fade-in">
            <div className="mb-8">
              <h1 className="text-[32px] font-bold tracking-[-0.02em] text-[#1D1D1F] mb-1">Presenters</h1>
              <p className="text-[#6E6E73] text-[17px]">Upload photos and manage your avatar collection.</p>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
              <AvatarUpload />
              <AvatarList selectedAvatar={selectedAvatar} onSelectAvatar={handleSelectAvatar} />
            </div>
            {selectedAvatar && (
              <div className="mt-8 flex justify-center">
                <button
                  onClick={() => setView("explainer")}
                  className="inline-flex items-center gap-2 text-[16px] font-semibold px-8 py-3.5 rounded-full text-white bg-[#0071E3] hover:bg-[#0077ED] transition-all active:scale-[0.98] group"
                >
                  <Film size={18} />
                  Make a video
                  <ChevronRight size={18} className="group-hover:translate-x-0.5 transition-transform" />
                </button>
              </div>
            )}
          </div>
        )}

        {/* ── VOICE VIEW ── */}
        {view === "voice" && (
          <div className="lightscope max-w-4xl mx-auto px-6 py-12 animate-fade-in">
            <div className="mb-8">
              <h1 className="text-[32px] font-bold tracking-[-0.02em] text-[#1D1D1F] mb-1">Voice Studio</h1>
              <p className="text-[#6E6E73] text-[17px]">Clone voices and manage your voice library.</p>
            </div>
            <VoicePanel onVoiceSelect={handleVoiceSelect} />
          </div>
        )}

        {/* ── STUDIO VIEW ── */}
        {view === "studio" && <StudioPanel />}

        {/* ── EXPLAINER / VIDEO VIEW ── */}
        {view === "explainer" && <ExplainerPanel />}

        {/* ── TUTORIAL VIEW ── */}
        {view === "tutorial" && <TutorialPanel />}
      </main>
    </div>
  );
}
