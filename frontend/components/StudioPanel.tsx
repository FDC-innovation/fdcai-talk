'use client'

import { useState, useRef, useCallback } from 'react'
import { api } from '@/lib/api'
import { toast } from 'react-hot-toast'
import {
  Upload,
  Scissors,
  Radio,
  Bot,
  Loader2,
  CheckCircle2,
  XCircle,
  Download,
  DownloadCloud,
  FileVideo,
  Film,
  Sparkles,
  Zap,
  Clock,
  FileText,
  ChevronDown,
  ChevronRight,
  RefreshCw,
} from 'lucide-react'

type Pipeline = 'clips' | 'podcast' | 'talking-head'
type JobStatus = 'idle' | 'uploading' | 'pending' | 'processing' | 'done' | 'failed'

interface JobResult {
  job_id: string
  status: string
  pipeline: string
  output: Record<string, unknown>
  error?: string
}

const PIPELINES: { id: Pipeline; icon: typeof Scissors; label: string; desc: string; color: string }[] = [
  {
    id: 'clips',
    icon: Scissors,
    label: 'Virality Clips',
    desc: 'Turn any long video into short, share-ready highlight clips — automatically.',
    color: 'from-violet-500 to-purple-600',
  },
  {
    id: 'podcast',
    icon: Radio,
    label: 'Podcast Studio',
    desc: 'Chapters, title cards, captions, and a polished final stitch — from raw audio.',
    color: 'from-blue-500 to-cyan-500',
  },
]

function statusColor(s: JobStatus) {
  if (s === 'done') return 'text-emerald-400'
  if (s === 'failed') return 'text-red-400'
  if (s === 'uploading' || s === 'pending' || s === 'processing') return 'text-amber-400'
  return 'text-gray-500'
}

function StatusBadge({ status }: { status: JobStatus }) {
  const labels: Record<JobStatus, string> = {
    idle: 'Idle',
    uploading: 'Uploading…',
    pending: 'Queued',
    processing: 'Processing…',
    done: 'Done',
    failed: 'Failed',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${statusColor(status)}`}>
      {(status === 'uploading' || status === 'pending' || status === 'processing') && (
        <Loader2 size={14} className="animate-spin" />
      )}
      {status === 'done' && <CheckCircle2 size={14} />}
      {status === 'failed' && <XCircle size={14} />}
      {labels[status]}
    </span>
  )
}

function fmtTime(sec?: number): string {
  if (sec == null || isNaN(sec)) return ''
  const s = Math.floor(sec % 60)
  const m = Math.floor(sec / 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

interface PreviewItem {
  title: string
  subtitle?: string
  reason?: string
  badge?: string
  artifact: string
}

function PreviewCard({ item, jobId }: { item: PreviewItem; jobId: string }) {
  const previewUrl = `${api.getJobDownloadUrl(jobId, item.artifact)}?inline=1`
  const downloadUrl = api.getJobDownloadUrl(jobId, item.artifact)
  return (
    <div className="rounded-xl overflow-hidden bg-white/5 border border-white/10 flex flex-col">
      <div className="relative bg-black aspect-video">
        <video src={previewUrl} controls preload="metadata" className="w-full h-full object-contain" />
        {item.badge && (
          <span className="absolute top-2 right-2 text-[11px] font-medium px-2 py-0.5 rounded-full bg-black/70 text-gray-200 flex items-center gap-1">
            <Clock size={10} />
            {item.badge}
          </span>
        )}
      </div>
      <div className="p-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-100 truncate">{item.title}</p>
          {item.subtitle && <p className="text-xs text-gray-400 truncate">{item.subtitle}</p>}
          {item.reason && (
            <p className="text-xs text-primary-300/80 mt-1 flex items-start gap-1">
              <Sparkles size={11} className="mt-0.5 shrink-0" />
              <span>{item.reason}</span>
            </p>
          )}
        </div>
        <a href={downloadUrl} download className="shrink-0 p-2 rounded-lg bg-white/5 hover:bg-white/10 transition-colors text-gray-300" title="Download">
          <Download size={16} />
        </a>
      </div>
    </div>
  )
}

function TranscriptSnippet({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const preview = text.length > 240 ? text.slice(0, 240).trimEnd() + '…' : text
  return (
    <div className="rounded-xl bg-white/5 border border-white/10 p-4">
      <div className="flex items-center gap-2 mb-2">
        <FileText size={14} className="text-gray-400" />
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-widest">Transcript</span>
      </div>
      <p className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed">{open ? text : preview}</p>
      {text.length > 240 && (
        <button onClick={() => setOpen((o) => !o)} className="mt-2 inline-flex items-center gap-1 text-xs text-primary-400 hover:text-primary-300 transition-colors">
          <ChevronDown size={12} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
          {open ? 'Show less' : 'Show full transcript'}
        </button>
      )}
    </div>
  )
}

async function downloadAll(items: { artifact: string }[], jobId: string) {
  for (const it of items) {
    const a = document.createElement('a')
    a.href = api.getJobDownloadUrl(jobId, it.artifact)
    a.download = ''
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    await new Promise((r) => setTimeout(r, 400))
  }
}

function friendlyError(raw?: string): { title: string; hint?: string; raw?: string } {
  const e = (raw || '').toLowerCase()
  if (e.includes('word-level timestamps') || e.includes('no clips') || e.includes('no speech')) {
    return {
      title: 'No speech detected in this video',
      hint: 'The Clip Cutter needs spoken audio to find highlights. Try a video with clear talking (a screen recording, interview, or podcast).',
      raw,
    }
  }
  if (e.includes('timed out')) {
    return { title: 'Processing timed out', hint: 'The video may be too long for this demo. Try a shorter clip.', raw }
  }
  return { title: 'Something went wrong', raw }
}

function DownloadRow({ label, url }: { label: string; url: string }) {
  return (
    <a
      href={url}
      download={true}
      className="flex items-center gap-3 px-4 py-3 rounded-xl bg-white/5 border border-white/8 hover:bg-white/10 hover:border-white/15 transition-all group"
    >
      <FileVideo size={16} className="text-gray-400 group-hover:text-primary-400 transition-colors" />
      <span className="text-sm text-gray-300 group-hover:text-white transition-colors flex-1">{label}</span>
      <Download size={14} className="text-gray-500 group-hover:text-primary-400 transition-colors" />
    </a>
  )
}

export function StudioPanel() {
  const [pipeline, setPipeline] = useState<Pipeline | null>(null)
  const [instructions, setInstructions] = useState('')
  const [model, setModel] = useState('llama3.2:1b')
  const [imageUrl, setImageUrl] = useState('')
  const [audioUrl, setAudioUrl] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [mediaPath, setMediaPath] = useState<string | null>(null)
  const [mediaName, setMediaName] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus>('idle')
  const [jobProgress, setJobProgress] = useState(0)
  const [jobResult, setJobResult] = useState<JobResult | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const pollJob = useCallback((jobId: string) => {
    pollRef.current = setInterval(async () => {
      try {
        const job = await api.getJob(jobId)
        if (job.status === 'done') {
          stopPolling()
          setJobStatus('done')
          setJobResult({ ...job, status: job.status, output: job.output ?? {} })
          toast.success('Job complete! Download your files below.', { icon: '🎬' })
        } else if (job.status === 'running') {
          setJobStatus('processing')
          setJobProgress(job.progress ?? 0)
        } else if (job.status === 'failed') {
          stopPolling()
          setJobStatus('failed')
          setJobResult({ ...job, status: job.status, output: {} })
          toast.error(job.error ?? 'Job failed')
        }
      } catch {
        // network blip — keep polling
      }
    }, 3000)
  }, [stopPolling])

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setJobStatus('uploading')
    setUploadProgress(0)
    setMediaPath(null)
    setJobResult(null)
    try {
      const result = await api.uploadMedia(file, setUploadProgress)
      setMediaPath(result.media_path)
      setMediaName(result.filename)
      setJobStatus('idle')
      toast.success(`Uploaded: ${result.filename}`, { icon: '📁' })
    } catch {
      setJobStatus('failed')
      toast.error('Upload failed')
    }
  }

  const handleRun = async () => {
    if (!pipeline) { toast('Pick a pipeline first', { icon: '👆' }); return }

    let params: Record<string, string> = {}

    if (pipeline === 'talking-head') {
      if (!imageUrl.trim() || !audioUrl.trim()) {
        toast('Talking Head needs an image URL and audio URL', { icon: '⚠️' })
        return
      }
      params = { image_url: imageUrl.trim(), audio_url: audioUrl.trim() }
    } else {
      if (!mediaPath) { toast('Upload a media file first', { icon: '⚠️' }); return }
      params = { media_path: mediaPath }
      if (instructions.trim()) params.instructions = instructions.trim()
      if (pipeline === 'clips') params.model = model
    }

    setJobStatus('pending')
    setJobResult(null)
    stopPolling()

    try {
      const { job_id } = await api.createJob(pipeline, params)
      setJobStatus('processing')
      pollJob(job_id)
    } catch {
      setJobStatus('failed')
      toast.error('Failed to start job')
    }
  }

  const handleReset = () => {
    stopPolling()
    setPipeline(null)
    setInstructions('')
    setImageUrl('')
    setAudioUrl('')
    setMediaPath(null)
    setMediaName(null)
    setUploadProgress(0)
    setJobStatus('idle')
    setJobResult(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // Build rich preview items from job output
  const previews: PreviewItem[] = []
  let heroItem: PreviewItem | null = null
  let transcript = ''
  let sourceDuration: number | undefined
  if (jobResult?.output && jobResult.status === 'done') {
    const out = jobResult.output as Record<string, unknown>
    transcript = typeof out.transcript === 'string' ? out.transcript : ''
    sourceDuration = typeof out.duration === 'number' ? out.duration : undefined
    if (pipeline === 'clips' && Array.isArray(out.clips)) {
      (out.clips as Record<string, unknown>[]).forEach((c, i) => {
        const start = typeof c.start_seconds === 'number' ? c.start_seconds : undefined
        const end = typeof c.end_seconds === 'number' ? c.end_seconds : undefined
        previews.push({
          title: (typeof c.title === 'string' && c.title) || `Clip ${i + 1}`,
          reason: typeof c.reason === 'string' ? c.reason : undefined,
          badge: start != null && end != null ? `${fmtTime(start)}–${fmtTime(end)}` : undefined,
          artifact: `clip_${i}`,
        })
      })
    } else if (pipeline === 'podcast') {
      heroItem = { title: 'Final Podcast', subtitle: 'Full stitched video', artifact: 'final' }
      if (Array.isArray(out.chapters)) {
        (out.chapters as Record<string, unknown>[]).forEach((c, i) => {
          const start = typeof c.start_seconds === 'number' ? c.start_seconds : undefined
          const end = typeof c.end_seconds === 'number' ? c.end_seconds : undefined
          previews.push({
            title: (typeof c.title === 'string' && c.title) || `Chapter ${i + 1}`,
            subtitle: typeof c.subtitle === 'string' ? c.subtitle : undefined,
            badge: start != null && end != null ? `${fmtTime(start)}–${fmtTime(end)}` : undefined,
            artifact: `chapter_${i}`,
          })
        })
      }
    } else if (pipeline === 'talking-head') {
      heroItem = { title: 'Talking Head', subtitle: 'Generated avatar video', artifact: 'final' }
    }
  }
  const allItems: PreviewItem[] = [...(heroItem ? [heroItem] : []), ...previews]
  const itemCountLabel =
    pipeline === 'clips' ? `${previews.length} clip${previews.length === 1 ? '' : 's'}`
    : pipeline === 'podcast' ? `${previews.length} chapter${previews.length === 1 ? '' : 's'}`
    : '1 video' 

  const isRunning = jobStatus === 'pending' || jobStatus === 'processing'
  const selectedPipeline = PIPELINES.find(p => p.id === pipeline)

  return (
    <div className="lightscope max-w-4xl mx-auto px-6 py-12 animate-fade-in">
      <div className="mb-10 flex items-start justify-between">
        <div>
          <p className="text-[13px] font-semibold tracking-[0.08em] uppercase text-[#0071E3] mb-2">Developer API</p>
          <h1 className="text-[32px] font-bold tracking-[-0.02em] text-[#1D1D1F] mb-1">API Pipelines</h1>
          <p className="text-[17px] text-[#6E6E73]">Upload media, pick a pipeline, and download your outputs.</p>
        </div>
        {jobStatus !== 'idle' && (
          <button
            onClick={handleReset}
            className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors px-3 py-1.5 rounded-lg hover:bg-white/5"
          >
            <RefreshCw size={14} />
            Reset
          </button>
        )}
      </div>

      {/* Step 1 — Pick pipeline */}
      <div className="mb-6">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">1 · Choose pipeline</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-2xl">
          {PIPELINES.map(({ id, icon: Icon, label, desc, color }) => (
            <button
              key={id}
              onClick={() => { setPipeline(id); setJobResult(null) }}
              className={`text-left p-5 rounded-2xl border transition-all duration-200
                ${pipeline === id
                  ? 'border-[#0071E3] bg-[#0071E3]/[0.05] shadow-[0_4px_20px_rgba(0,113,227,0.12)]'
                  : 'border-[#E5E5EA] bg-white hover:border-[#0071E3]/40 shadow-[0_2px_12px_rgba(0,0,0,0.04)]'
                }`}
            >
              <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${color} flex items-center justify-center mb-3`}>
                <Icon size={17} className="text-white" />
              </div>
              <div className="font-semibold text-[15px] text-[#1D1D1F] mb-1">{label}</div>
              <div className="text-[13px] text-[#6E6E73] leading-relaxed">{desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Step 2 — Input */}
      {pipeline && (
        <div className="mb-6 animate-fade-in">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">2 · Provide input</p>

          {pipeline === 'talking-head' ? (
            <div className="space-y-3">
              <input
                type="url"
                placeholder="Image URL (publicly accessible)"
                value={imageUrl}
                onChange={e => setImageUrl(e.target.value)}
                className="w-full px-4 py-3 rounded-xl bg-surface-800 border border-white/8 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-primary-500/50 focus:bg-surface-700 transition-all"
              />
              <input
                type="url"
                placeholder="Audio URL (publicly accessible)"
                value={audioUrl}
                onChange={e => setAudioUrl(e.target.value)}
                className="w-full px-4 py-3 rounded-xl bg-surface-800 border border-white/8 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-primary-500/50 focus:bg-surface-700 transition-all"
              />
            </div>
          ) : (
            <div className="space-y-3">
              {/* Upload zone */}
              <div
                onClick={() => fileInputRef.current?.click()}
                className={`relative flex flex-col items-center justify-center gap-3 p-8 rounded-2xl border-2 border-dashed cursor-pointer transition-all
                  ${mediaPath
                    ? 'border-emerald-500/40 bg-emerald-500/5'
                    : 'border-white/10 bg-white/2 hover:border-primary-500/40 hover:bg-primary-500/5'
                  }`}
              >
                {jobStatus === 'uploading' ? (
                  <>
                    <Loader2 size={28} className="text-primary-400 animate-spin" />
                    <p className="text-sm text-gray-400">Uploading… {uploadProgress}%</p>
                    <div className="w-full max-w-xs h-1.5 rounded-full bg-white/10 overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-primary-500 to-accent-500 transition-all duration-300"
                        style={{ width: `${uploadProgress}%` }}
                      />
                    </div>
                  </>
                ) : mediaPath ? (
                  <>
                    <CheckCircle2 size={28} className="text-emerald-400" />
                    <p className="text-sm text-emerald-300 font-medium">{mediaName}</p>
                    <p className="text-xs text-gray-500">Click to replace</p>
                  </>
                ) : (
                  <>
                    <Upload size={28} className="text-gray-500" />
                    <p className="text-sm text-gray-400">Click to upload video or audio</p>
                    <p className="text-xs text-gray-600">MP4, MOV, WAV, MP3 · max 500 MB</p>
                  </>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="video/mp4,video/quicktime,audio/wav,audio/mpeg,audio/mp4"
                  className="hidden"
                  onChange={handleFileChange}
                />
              </div>

              {/* Instructions */}
              <input
                type="text"
                placeholder={pipeline === 'podcast' ? 'Instructions (e.g. "3 chapters") — optional' : 'Instructions — optional'}
                value={instructions}
                onChange={e => setInstructions(e.target.value)}
                className="w-full px-4 py-3 rounded-xl bg-surface-800 border border-white/8 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-primary-500/50 focus:bg-surface-700 transition-all"
              />
            </div>
          )}
        </div>
      )}

      {/* Step 3 — Run */}
      {pipeline && (
        <div className="mb-8">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">3 · Run</p>
          {pipeline === 'clips' && (
            <div className="mb-4">
              <p className="text-xs text-gray-500 mb-2">AI model</p>
              <div className="inline-flex rounded-xl bg-white/5 border border-white/10 p-1 gap-1">
                <button
                  onClick={() => setModel('llama3.2:1b')}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-1.5 ${
                    model === 'llama3.2:1b'
                      ? 'bg-primary-500/20 text-primary-300 border border-primary-500/30'
                      : 'text-gray-400 hover:text-gray-200'
                  }`}
                >
                  <Zap size={14} />
                  Fast
                </button>
                <button
                  disabled
                  title="Coming soon"
                  className="px-4 py-2 rounded-lg text-sm font-medium text-gray-600 cursor-not-allowed flex items-center gap-1.5 relative"
                >
                  <Sparkles size={14} />
                  Quality
                  <span className="text-[9px] uppercase tracking-wide bg-white/10 text-gray-400 px-1.5 py-0.5 rounded-full ml-1">Soon</span>
                </button>
              </div>
            </div>
          )}
          <div className="flex items-center gap-4">
            <button
              onClick={handleRun}
              disabled={isRunning || jobStatus === 'uploading'}
              className="btn-primary px-8 py-3 rounded-2xl group disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isRunning ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  {jobStatus === 'pending' ? 'Queued…' : `Processing… ${jobProgress}%`}
                </>
              ) : (
                <>
                  {selectedPipeline && <selectedPipeline.icon size={18} />}
                  Run {selectedPipeline?.label}
                  <ChevronRight size={16} className="group-hover:translate-x-1 transition-transform" />
                </>
              )}
            </button>
            <StatusBadge status={jobStatus} />
          </div>
        </div>
      )}

      {/* Results */}
      {jobStatus === 'done' && allItems.length > 0 && jobResult && (
        <div className="animate-fade-in space-y-5">
          <div className="flex items-center justify-between gap-3 flex-wrap p-4 rounded-xl bg-gradient-to-r from-emerald-500/10 to-teal-500/10 border border-emerald-500/20">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-emerald-500/20">
                <Film size={18} className="text-emerald-400" />
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-100">Results ready</p>
                <p className="text-xs text-gray-400">
                  {itemCountLabel}
                  {sourceDuration != null && ` · ${fmtTime(sourceDuration)} source`}
                </p>
              </div>
            </div>
            <button
              onClick={() => downloadAll(allItems, jobResult.job_id)}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 border border-white/15 text-sm font-medium text-gray-100 transition-colors"
            >
              <DownloadCloud size={16} />
              Download all
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {allItems.map((item) => (
              <PreviewCard key={item.artifact} item={item} jobId={jobResult.job_id} />
            ))}
          </div>
          {transcript && <TranscriptSnippet text={transcript} />}
        </div>
      )}

      {jobStatus === 'failed' && jobResult?.error && (() => {
        const fe = friendlyError(jobResult.error)
        return (
          <div className="animate-fade-in p-4 rounded-xl bg-red-500/10 border border-red-500/20">
            <p className="text-sm font-semibold text-red-200">{fe.title}</p>
            {fe.hint && <p className="text-sm text-red-300/80 mt-1">{fe.hint}</p>}
            {fe.raw && (
              <details className="mt-2">
                <summary className="text-xs text-red-400/70 cursor-pointer">Technical details</summary>
                <p className="text-xs text-red-400/60 mt-1 font-mono break-all">{fe.raw}</p>
              </details>
            )}
          </div>
        )
      })()}
    </div>
  )
}
