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
  FileVideo,
  ChevronRight,
  RefreshCw,
} from 'lucide-react'

type Pipeline = 'clips' | 'podcast' | 'sadtalker'
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
    label: 'Clip Cutter',
    desc: 'Auto-detect and cut highlight clips from any video.',
    color: 'from-violet-500 to-purple-600',
  },
  {
    id: 'podcast',
    icon: Radio,
    label: 'Podcast',
    desc: 'Chapter detection, title cards, captions, and final stitch.',
    color: 'from-blue-500 to-cyan-500',
  },
  {
    id: 'sadtalker',
    icon: Bot,
    label: 'SadTalker',
    desc: 'Animate a face photo with an audio file (GPU required).',
    color: 'from-emerald-500 to-teal-500',
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
  const [imageUrl, setImageUrl] = useState('')
  const [audioUrl, setAudioUrl] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [mediaPath, setMediaPath] = useState<string | null>(null)
  const [mediaName, setMediaName] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<JobStatus>('idle')
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

    if (pipeline === 'sadtalker') {
      if (!imageUrl.trim() || !audioUrl.trim()) {
        toast('SadTalker needs an image URL and audio URL', { icon: '⚠️' })
        return
      }
      params = { image_url: imageUrl.trim(), audio_url: audioUrl.trim() }
    } else {
      if (!mediaPath) { toast('Upload a media file first', { icon: '⚠️' }); return }
      params = { media_path: mediaPath }
      if (instructions.trim()) params.instructions = instructions.trim()
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

  // Build download links from job output
  const downloads: { label: string; url: string }[] = []
  if (jobResult?.output && jobResult.status === 'done') {
    const out = jobResult.output
    if (pipeline === 'clips' && Array.isArray(out.clips)) {
      out.clips.forEach((_: unknown, i: number) => {
        downloads.push({
          label: `Clip ${i + 1}`,
          url: api.getJobDownloadUrl(jobResult.job_id, `clip_${i}`),
        })
      })
    } else if (pipeline === 'podcast') {
      if (Array.isArray(out.chapters)) {
        out.chapters.forEach((_: unknown, i: number) => {
          downloads.push({
            label: `Chapter ${i + 1}`,
            url: api.getJobDownloadUrl(jobResult.job_id, `chapter_${i}`),
          })
        })
      }
      downloads.push({ label: 'Final Video', url: api.getJobDownloadUrl(jobResult.job_id, 'final') })
    } else if (pipeline === 'sadtalker') {
      downloads.push({ label: 'Animated Video', url: api.getJobDownloadUrl(jobResult.job_id, 'final') })
    }
  }

  const isRunning = jobStatus === 'pending' || jobStatus === 'processing'
  const selectedPipeline = PIPELINES.find(p => p.id === pipeline)

  return (
    <div className="max-w-3xl mx-auto px-6 py-10 animate-fade-in">
      <div className="mb-8 flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-black gradient-text mb-2">Studio</h1>
          <p className="text-gray-400">Upload media, pick a pipeline, and download your outputs.</p>
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
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {PIPELINES.map(({ id, icon: Icon, label, desc, color }) => (
            <button
              key={id}
              onClick={() => { setPipeline(id); setJobResult(null) }}
              className={`text-left p-4 rounded-2xl border transition-all duration-200
                ${pipeline === id
                  ? 'border-primary-500/60 bg-primary-500/10 shadow-glow-sm'
                  : 'border-white/8 bg-white/3 hover:border-white/15 hover:bg-white/6'
                }`}
            >
              <div className={`w-9 h-9 rounded-xl bg-gradient-to-br ${color} flex items-center justify-center mb-3`}>
                <Icon size={16} className="text-white" />
              </div>
              <div className="font-semibold text-sm text-white mb-1">{label}</div>
              <div className="text-xs text-gray-500 leading-relaxed">{desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Step 2 — Input */}
      {pipeline && (
        <div className="mb-6 animate-fade-in">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">2 · Provide input</p>

          {pipeline === 'sadtalker' ? (
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
          <div className="flex items-center gap-4">
            <button
              onClick={handleRun}
              disabled={isRunning || jobStatus === 'uploading'}
              className="btn-primary px-8 py-3 rounded-2xl group disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isRunning ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  {jobStatus === 'pending' ? 'Queued…' : 'Processing…'}
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
      {jobStatus === 'done' && downloads.length > 0 && (
        <div className="animate-fade-in">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">4 · Download outputs</p>
          <div className="space-y-2">
            {downloads.map(({ label, url }) => (
              <DownloadRow key={label} label={label} url={url} />
            ))}
          </div>
        </div>
      )}

      {jobStatus === 'failed' && jobResult?.error && (
        <div className="animate-fade-in p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-sm text-red-300">
          {jobResult.error}
        </div>
      )}
    </div>
  )
}
