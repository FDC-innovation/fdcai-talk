'use client'

import {
  Camera, Film, Mic, Scissors, Radio,
  Sparkles, ArrowRight, Lightbulb,
} from 'lucide-react'

type Guide = {
  id: string
  icon: typeof Film
  eyebrow: string
  title: string
  blurb: string
  steps: string[]
  tip?: string
}

const GUIDES: Guide[] = [
  {
    id: 'avatars',
    icon: Camera,
    eyebrow: 'Presenters',
    title: 'Add an avatar',
    blurb: 'Every video needs a face. Upload a clear photo and it becomes a reusable presenter.',
    steps: [
      'Open the Avatars tab.',
      'Drag in a photo, or click to browse — a clear, front-facing headshot with good lighting works best.',
      'Give it a name (e.g. “News Anchor”) and upload.',
      'Once it shows as ready, it’s available as a presenter in the Video studio.',
    ],
    tip: 'Front-facing, evenly lit photos produce the cleanest lip-sync.',
  },
  {
    id: 'voice',
    icon: Mic,
    eyebrow: 'Voice cloning',
    title: 'Clone a voice',
    blurb: 'Give your videos a specific voice by cloning it from a short reference clip.',
    steps: [
      'In the Video studio, turn on the “Clone a voice” toggle.',
      'Choose “Upload new”, then pick a clean 10–60 second audio clip of the target voice.',
      'Name the voice and click “Clone this voice”.',
      'It’s saved to your library — select it any time under “Saved voices”.',
    ],
    tip: 'A calm, clearly-spoken 15–30s sample with no background noise clones best.',
  },
  {
    id: 'video',
    icon: Film,
    eyebrow: 'Video studio',
    title: 'Make a narrated video',
    blurb: 'Turn a deck walkthrough into avatar-narrated clips — one per section, automatically.',
    steps: [
      'Open the Video tab.',
      'Upload your deck walkthrough video (MP4, MOV, or WEBM).',
      'Choose a presenter, and optionally toggle on a cloned voice.',
      'Pick a language and press Generate.',
      'Watch the live stages — transcribing, scripting, voicing, animating — then preview and download each clip.',
    ],
    tip: 'Shorter source videos generate faster; each section becomes its own downloadable clip.',
  },
  {
    id: 'clips',
    icon: Scissors,
    eyebrow: 'API · Virality Clips',
    title: 'Cut viral highlight clips',
    blurb: 'Drop in any long video and automatically get short, share-ready highlight moments.',
    steps: [
      'Open the API tab and choose Virality Clips.',
      'Upload a video with clear spoken audio (talks, interviews, streams work great).',
      'Optionally add instructions, then press Run.',
      'When it finishes, preview the detected highlights and download them individually or all at once.',
    ],
    tip: 'The engine needs speech to find highlights — pick videos with clear talking.',
  },
  {
    id: 'podcast',
    icon: Radio,
    eyebrow: 'API · Podcast Studio',
    title: 'Produce a podcast',
    blurb: 'Transform raw audio into a chaptered, captioned, polished final cut.',
    steps: [
      'Open the API tab and choose Podcast Studio.',
      'Upload your raw audio or video recording.',
      'Optionally tell it how many chapters you want, then press Run.',
      'Download the chaptered clips, title cards, captions, and the final stitched output.',
    ],
    tip: 'Add an instruction like “3 chapters” to guide how the episode is segmented.',
  },
]

export function TutorialPanel() {
  return (
    <div className="lightscope max-w-4xl mx-auto px-6 py-12 animate-fade-in" style={{ color: '#1D1D1F' }}>
      {/* Header */}
      <div className="text-center mb-12">
        <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-[#0071E3]/[0.08] mb-4">
          <Sparkles size={13} className="text-[#0071E3]" />
          <span className="text-[13px] text-[#0071E3] font-semibold">Getting started</span>
        </div>
        <h1 className="text-[40px] font-bold tracking-[-0.02em] text-[#1D1D1F] mb-3">
          Learn Chalchitra in five short guides.
        </h1>
        <p className="text-[19px] text-[#6E6E73] max-w-2xl mx-auto leading-relaxed">
          Everything the studio can do — from adding a presenter to producing a full podcast —
          explained step by step.
        </p>
      </div>

      {/* Quick nav chips */}
      <div className="flex flex-wrap justify-center gap-2 mb-12">
        {GUIDES.map((g) => (
          <a key={g.id} href={`#${g.id}`}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-white border border-[#E5E5EA] text-[13px] font-medium text-[#1D1D1F] hover:border-[#0071E3]/40 transition"
            style={{ boxShadow: '0 2px 10px rgba(0,0,0,0.04)' }}>
            <g.icon size={14} className="text-[#0071E3]" />
            {g.title}
          </a>
        ))}
      </div>

      {/* Guides */}
      <div className="space-y-6">
        {GUIDES.map((g, gi) => (
          <div key={g.id} id={g.id}
            className="bg-white rounded-[20px] border border-[#EDEDF0] p-7 scroll-mt-24"
            style={{ boxShadow: '0 4px 24px rgba(0,0,0,0.06)' }}>
            <div className="flex items-start gap-4 mb-6">
              <div className="w-12 h-12 rounded-2xl bg-[#0071E3]/[0.08] flex items-center justify-center flex-shrink-0">
                <g.icon size={22} className="text-[#0071E3]" />
              </div>
              <div className="min-w-0">
                <p className="text-[12px] font-semibold tracking-[0.06em] uppercase text-[#0071E3] mb-1">
                  {String(gi + 1).padStart(2, '0')} · {g.eyebrow}
                </p>
                <h2 className="text-[22px] font-bold tracking-[-0.01em] text-[#1D1D1F] leading-tight">{g.title}</h2>
                <p className="text-[15px] text-[#6E6E73] mt-1 leading-relaxed">{g.blurb}</p>
              </div>
            </div>

            <ol className="space-y-3 mb-1">
              {g.steps.map((step, i) => (
                <li key={i} className="flex items-start gap-3">
                  <span className="flex items-center justify-center w-6 h-6 rounded-full bg-[#F0F0F2] text-[#1D1D1F] text-[12px] font-semibold flex-shrink-0 mt-0.5">
                    {i + 1}
                  </span>
                  <span className="text-[15px] text-[#1D1D1F] leading-relaxed">{step}</span>
                </li>
              ))}
            </ol>

            {g.tip && (
              <div className="mt-5 flex items-start gap-2.5 px-4 py-3 rounded-xl bg-[#FFFBEB] border border-[#FBE5B8]">
                <Lightbulb size={16} className="text-[#9A6400] flex-shrink-0 mt-0.5" />
                <p className="text-[13px] text-[#7A5200] leading-relaxed">{g.tip}</p>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Footer note */}
      <div className="mt-12 text-center">
        <div className="inline-flex items-center gap-2 text-[14px] text-[#6E6E73]">
          <span>That’s the whole studio.</span>
          <span className="inline-flex items-center gap-1 text-[#0071E3] font-medium">
            Jump into the Video tab to try it <ArrowRight size={14} />
          </span>
        </div>
      </div>
    </div>
  )
}
