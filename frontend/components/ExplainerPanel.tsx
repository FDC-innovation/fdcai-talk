'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import {
  Upload, Loader2, X, CheckCircle2, Film, AlertCircle,
  Download, Play, Video, ImagePlus, Check, Mic, AudioLines,
} from 'lucide-react'
import { api, apiClient } from '@/lib/api'
import type { Avatar } from '@/lib/types'

type Clip = {
  index: number
  title: string
  script: string
  clip_path?: string
  engine?: string
  size_bytes?: number
}

type JobState = {
  job_id: string
  status: string
  progress: number
  output: { clips?: Clip[]; engine?: string; transcript?: string }
  error?: string | null
}

type Voice = { id: string; name: string; language: string; duration: number }

const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Spanish' },
  { code: 'fr', label: 'French' },
  { code: 'de', label: 'German' },
  { code: 'hi', label: 'Hindi' },
]

// Stepper stages mapped to the REAL backend progress ladder:
// running→10 transcribe+sections, 45 sections done, 55 avatar fetched,
// 55–90 per-section voice+animate, 90 clips done, 100 done.
const STAGES = [
  { key: 'transcribe', label: 'Transcribing deck', detail: 'Reading the walkthrough with Whisper', from: 0, to: 10 },
  { key: 'sections', label: 'Writing sections', detail: 'Breaking it into short scripts', from: 10, to: 45 },
  { key: 'avatar', label: 'Preparing avatar', detail: 'Loading your presenter', from: 45, to: 55 },
  { key: 'clips', label: 'Voicing & animating', detail: 'Cloned voice + lip-synced video per section', from: 55, to: 90 },
  { key: 'finish', label: 'Finishing up', detail: 'Assembling your clips', from: 90, to: 100 },
]

function stageStatus(stageFrom: number, stageTo: number, progress: number, jobStatus: string) {
  if (jobStatus === 'failed') {
    // mark the stage the failure landed in as errored, earlier ones done
    if (progress >= stageTo) return 'done'
    if (progress >= stageFrom) return 'error'
    return 'idle'
  }
  if (progress >= stageTo) return 'done'
  if (progress >= stageFrom) return 'active'
  return 'idle'
}

export function ExplainerPanel() {
  // deck video upload
  const [deckFile, setDeckFile] = useState<File | null>(null)
  const [deckName, setDeckName] = useState('')
  const [uploadPct, setUploadPct] = useState(0)
  const [mediaPath, setMediaPath] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [deckDrag, setDeckDrag] = useState(false)

  // selections
  const [avatarId, setAvatarId] = useState<string | null>(null)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [language, setLanguage] = useState('en')

  // voice cloning (optional)
  const [cloneOn, setCloneOn] = useState(false)
  const [voiceMode, setVoiceMode] = useState<'pick' | 'upload'>('pick')
  const [voiceId, setVoiceId] = useState<string | null>(null)
  const [voiceFile, setVoiceFile] = useState<File | null>(null)
  const [voiceName, setVoiceName] = useState('')
  const [voiceErr, setVoiceErr] = useState<string | null>(null)

  // job
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<JobState | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // clip object URLs (auth-fetched blobs)
  const [clipUrls, setClipUrls] = useState<Record<number, string>>({})
  const objectUrls = useRef<string[]>([])

  const queryClient = useQueryClient()

  // inline avatar upload (shown when there are no ready avatars)
  const [avUploadPreview, setAvUploadPreview] = useState<string | null>(null)
  const [avUploadFileName, setAvUploadFileName] = useState('')
  const [avUploadName, setAvUploadName] = useState('')
  const [avUploadError, setAvUploadError] = useState<string | null>(null)
  const [avDragActive, setAvDragActive] = useState(false)

  const avatarUploadMutation = useMutation({
    mutationFn: (formData: FormData) => api.uploadAvatar(formData),
    onSuccess: () => {
      toast.success('Avatar uploaded', { icon: '✨' })
      setAvUploadPreview(null)
      setAvUploadName('')
      setAvUploadFileName('')
      setAvUploadError(null)
      queryClient.invalidateQueries({ queryKey: ['avatars'] })
    },
    onError: () => {
      toast.error('Avatar upload failed — please try again')
    },
  })

  const processAvatarFile = (file: File) => {
    setAvUploadError(null)
    if (!file.type.startsWith('image/')) {
      setAvUploadError('Please upload a JPG, PNG, or WEBP image.')
      return
    }
    if (file.size > 10 * 1024 * 1024) {
      setAvUploadError('File must be under 10 MB.')
      return
    }
    setAvUploadFileName(file.name)
    if (!avUploadName) setAvUploadName(file.name.replace(/\.[^/.]+$/, ''))
    const reader = new FileReader()
    reader.onload = (e) => setAvUploadPreview(e.target?.result as string)
    reader.readAsDataURL(file)
  }

  const submitAvatarUpload = () => {
    if (!avUploadPreview || !avUploadName.trim()) {
      setAvUploadError('Please give your avatar a name.')
      return
    }
    fetch(avUploadPreview)
      .then((res) => res.blob())
      .then((blob) => {
        const formData = new FormData()
        formData.append('file', blob, avUploadFileName || 'avatar.jpg')
        formData.append('name', avUploadName.trim())
        avatarUploadMutation.mutate(formData)
      })
  }

  const { data: avatars } = useQuery<Avatar[]>({
    queryKey: ['avatars'],
    queryFn: api.getAvatars,
  })

  const { data: voices } = useQuery<Voice[]>({
    queryKey: ['voices'],
    queryFn: async () => {
      const d = await api.listVoices()
      return Array.isArray(d) ? d : (d?.voices ?? d?.items ?? [])
    },
  })

  const cloneMutation = useMutation({
    mutationFn: ({ file, name }: { file: File; name: string }) =>
      api.cloneVoice(file, name, language),
    onSuccess: (v: { id: string }) => {
      toast.success('Voice cloned', { icon: '\uD83C\uDFA4' })
      setVoiceId(v.id)
      setVoiceMode('pick')
      setVoiceFile(null)
      setVoiceName('')
      setVoiceErr(null)
      queryClient.invalidateQueries({ queryKey: ['voices'] })
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setVoiceErr(msg || 'Could not clone voice. Use a clear 10\u201360s clip.')
      toast.error('Voice cloning failed')
    },
  })

  const pickVoiceFile = (file: File) => {
    setVoiceErr(null)
    const okType = /audio\/(wav|x-wav|mpeg|mp3|webm|ogg)/.test(file.type) || /\.(wav|mp3|webm|ogg|m4a)$/i.test(file.name)
    if (!okType) { setVoiceErr('Please use a WAV, MP3, or WEBM audio file.'); return }
    if (file.size > 20 * 1024 * 1024) { setVoiceErr('Audio must be under 20 MB.'); return }
    setVoiceFile(file)
    if (!voiceName) setVoiceName(file.name.replace(/\.[^/.]+$/, ''))
  }

  const submitClone = () => {
    if (!voiceFile) { setVoiceErr('Choose an audio clip first.'); return }
    if (!voiceName.trim()) { setVoiceErr('Give the voice a name.'); return }
    cloneMutation.mutate({ file: voiceFile, name: voiceName.trim() })
  }

  const readyAvatars = (avatars ?? []).filter(
    (a) => a.status === 'ready' && (a.image_url || a.thumbnail_url),
  )

  // ── deck upload ──
  const handleDeckPick = (file: File) => {
    setError(null)
    if (!file.type.startsWith('video/')) {
      setError('Please upload a video file (MP4, MOV, WEBM).')
      return
    }
    if (file.size > 500 * 1024 * 1024) {
      setError('Video must be under 500 MB.')
      return
    }
    setDeckFile(file)
    setDeckName(file.name)
    setMediaPath(null)
    setUploadPct(0)
  }

  const uploadDeck = async () => {
    if (!deckFile) return
    setUploading(true)
    setError(null)
    try {
      const res = await api.uploadMedia(deckFile, setUploadPct)
      setMediaPath(res.media_path)
      toast.success('Deck video uploaded', { icon: '🎬' })
    } catch {
      setError('Upload failed — please try again.')
      toast.error('Deck upload failed')
    } finally {
      setUploading(false)
    }
  }

  // ── start job ──
  const startJob = async () => {
    if (!mediaPath) { setError('Upload a deck video first.'); return }
    if (!imageUrl) { setError('Pick an avatar first.'); return }
    setStarting(true)
    setError(null)
    setJob(null)
    setClipUrls({})
    try {
      const jobParams: Record<string, string> = {
        media_path: mediaPath,
        image_url: imageUrl,
        language,
      }
      if (cloneOn && voiceId) jobParams.voice_id = voiceId
      const { job_id } = await api.createJob('explainer', jobParams)
      setJobId(job_id)
      toast.success('Generating your explainer', { icon: '✨' })
    } catch {
      setError('Could not start the job. Check you are signed in.')
      toast.error('Failed to start job')
    } finally {
      setStarting(false)
    }
  }

  // ── poll ──
  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    const tick = async () => {
      try {
        const j = await api.getJob(jobId)
        if (cancelled) return
        setJob(j as JobState)
        if (j.status === 'done' || j.status === 'failed') return true
      } catch {
        /* transient — keep polling */
      }
      return false
    }
    let timer: ReturnType<typeof setTimeout>
    const loop = async () => {
      const done = await tick()
      if (!done && !cancelled) timer = setTimeout(loop, 2500)
    }
    loop()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [jobId])

  // ── fetch clip blobs once done (auth-protected, <video> can't send headers) ──
  const loadClips = useCallback(async (clips: Clip[]) => {
    const next: Record<number, string> = {}
    for (const clip of clips) {
      try {
        const url = api.getJobDownloadUrl(jobId!, `clip_${clip.index}`)
        const resp = await apiClient.get(url, { responseType: 'blob' })
        const obj = URL.createObjectURL(resp.data as Blob)
        objectUrls.current.push(obj)
        next[clip.index] = obj
      } catch {
        /* skip a clip that fails to load */
      }
    }
    setClipUrls(next)
  }, [jobId])

  useEffect(() => {
    if (job?.status === 'done' && job.output?.clips?.length) {
      loadClips(job.output.clips)
    }
  }, [job?.status, job?.output?.clips, loadClips])

  // cleanup object URLs on unmount
  useEffect(() => () => { objectUrls.current.forEach(URL.revokeObjectURL) }, [])

  const downloadClip = (clip: Clip, obj: string) => {
    const a = document.createElement('a')
    a.href = obj
    a.download = `${(clip.title || 'clip').replace(/[^a-z0-9]+/gi, '_').toLowerCase()}.mp4`
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  const resetAll = () => {
    setDeckFile(null); setDeckName(''); setMediaPath(null); setUploadPct(0)
    setJobId(null); setJob(null); setClipUrls({}); setError(null)
    objectUrls.current.forEach(URL.revokeObjectURL); objectUrls.current = []
  }

  const progress = job?.progress ?? 0
  const jobStatus = job?.status ?? ''
  const isBusy = starting || (!!job && (jobStatus === 'processing' || jobStatus === 'pending' || jobStatus === 'running'))
  const isDone = jobStatus === 'done'
  const isFailed = jobStatus === 'failed'
  const clips = job?.output?.clips ?? []
  const showRun = isBusy || isDone || isFailed

  return (
    <div style={{ background: '#FBFBFD', minHeight: '100%' }} className="text-[#1D1D1F]">
      <div className="max-w-6xl mx-auto px-6 py-14">

        {/* ── Header ── */}
        <header className="mb-12 text-center">
          <p className="text-[13px] font-semibold tracking-[0.08em] uppercase text-[#0071E3] mb-3">
            FDC AI Studio
          </p>
          <h1 className="text-[40px] leading-[1.05] font-bold tracking-[-0.02em] text-[#1D1D1F] mb-3">
            Turn a deck into a narrated video.
          </h1>
          <p className="text-[19px] leading-relaxed text-[#6E6E73] max-w-2xl mx-auto">
            Upload your walkthrough, choose a presenter, and let the studio transcribe,
            script, voice, and animate it — one clip per section.
          </p>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.1fr] gap-6 items-start">

          {/* ── LEFT: inputs ── */}
          <div className="bg-white rounded-[20px] border border-[#EDEDF0] p-7"
               style={{ boxShadow: '0 4px 24px rgba(0,0,0,0.06)' }}>

            {/* Step 1: deck */}
            <div className="flex items-baseline gap-2.5 mb-4">
              <span className="flex items-center justify-center w-6 h-6 rounded-full bg-[#0071E3] text-white text-[13px] font-semibold flex-shrink-0">1</span>
              <div>
                <h2 className="text-[17px] font-semibold text-[#1D1D1F] leading-tight">Upload deck video</h2>
                <p className="text-[13px] text-[#86868B]">MP4, MOV, or WEBM · up to 500 MB</p>
              </div>
            </div>

            {!deckFile ? (
              <label
                htmlFor="deck-upload"
                onDragEnter={(e) => { e.preventDefault(); setDeckDrag(true) }}
                onDragOver={(e) => { e.preventDefault(); setDeckDrag(true) }}
                onDragLeave={(e) => { e.preventDefault(); setDeckDrag(false) }}
                onDrop={(e) => { e.preventDefault(); setDeckDrag(false); if (e.dataTransfer.files?.[0]) handleDeckPick(e.dataTransfer.files[0]) }}
                className={`relative block rounded-2xl border-2 border-dashed p-9 text-center cursor-pointer transition-all duration-200
                  ${deckDrag ? 'border-[#0071E3] bg-[#0071E3]/[0.04]' : 'border-[#D2D2D7] hover:border-[#0071E3]/60 hover:bg-[#F5F5F7]'}`}
              >
                <input
                  type="file" id="deck-upload" accept="video/*"
                  onChange={(e) => e.target.files?.[0] && handleDeckPick(e.target.files[0])}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                />
                <div className="pointer-events-none flex flex-col items-center gap-3">
                  <div className="w-14 h-14 rounded-2xl bg-[#F5F5F7] flex items-center justify-center">
                    <Video size={24} className="text-[#86868B]" />
                  </div>
                  <div>
                    <p className="text-[#1D1D1F] font-semibold text-[15px]">Drag & drop, or click to choose</p>
                    <p className="text-[#86868B] text-[13px] mt-0.5">Your recorded deck walkthrough</p>
                  </div>
                </div>
              </label>
            ) : (
              <div className="rounded-2xl border border-[#E5E5EA] p-4 flex flex-col gap-3 bg-[#FAFAFC]">
                <div className="flex items-center gap-2.5">
                  <Film size={17} className="text-[#0071E3] flex-shrink-0" />
                  <span className="text-[14px] text-[#1D1D1F] truncate flex-1">{deckName}</span>
                  {!mediaPath && !uploading && (
                    <button onClick={() => { setDeckFile(null); setDeckName('') }}
                      className="text-[#86868B] hover:text-[#D70015] transition-colors">
                      <X size={16} />
                    </button>
                  )}
                  {mediaPath && <CheckCircle2 size={17} className="text-[#30D158] flex-shrink-0" />}
                </div>

                {uploading && (
                  <div className="h-1.5 rounded-full bg-[#E5E5EA] overflow-hidden">
                    <div className="h-full bg-[#0071E3] rounded-full transition-all duration-200"
                      style={{ width: `${uploadPct}%` }} />
                  </div>
                )}

                {!mediaPath && !uploading && (
                  <button onClick={uploadDeck}
                    className="w-full py-2.5 rounded-full text-[14px] font-semibold text-white bg-[#0071E3] hover:bg-[#0077ED] transition-colors flex items-center justify-center gap-2 active:scale-[0.98]">
                    <Upload size={15} /> Upload video
                  </button>
                )}
                {uploading && <p className="text-[12px] text-center text-[#86868B]">Uploading… {uploadPct}%</p>}
                {mediaPath && <p className="text-[12px] text-center text-[#1D7A3E]">Uploaded — ready to generate</p>}
              </div>
            )}

            <div className="h-px bg-[#E5E5EA] my-6" />

            {/* Step 2: avatar */}
            <div className="flex items-baseline gap-2.5 mb-4">
              <span className="flex items-center justify-center w-6 h-6 rounded-full bg-[#0071E3] text-white text-[13px] font-semibold flex-shrink-0">2</span>
              <div>
                <h2 className="text-[17px] font-semibold text-[#1D1D1F] leading-tight">Choose a presenter</h2>
                <p className="text-[13px] text-[#86868B]">The face for every clip</p>
              </div>
            </div>

            {readyAvatars.length === 0 ? (
              <div className="rounded-2xl bg-[#FAFAFC] border border-[#E5E5EA] p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <AlertCircle size={14} className="text-[#86868B] flex-shrink-0" />
                  <p className="text-[13px] text-[#6E6E73]">No presenters yet — add one here.</p>
                </div>

                {avUploadPreview ? (
                  <div className="flex items-center gap-3">
                    <img src={avUploadPreview} alt="preview" className="w-16 h-16 rounded-xl object-cover border border-[#E5E5EA]" />
                    <div className="flex-1 min-w-0">
                      <input
                        type="text" value={avUploadName}
                        onChange={(e) => setAvUploadName(e.target.value)}
                        placeholder="Presenter name"
                        className="w-full px-3.5 py-2.5 rounded-xl bg-white border border-[#D2D2D7] text-[14px] text-[#1D1D1F] placeholder:text-[#AEAEB2] focus:outline-none focus:ring-4 focus:ring-[#0071E3]/15 focus:border-[#0071E3] transition"
                      />
                    </div>
                    <button
                      onClick={() => { setAvUploadPreview(null); setAvUploadFileName(''); setAvUploadError(null) }}
                      className="w-9 h-9 flex items-center justify-center rounded-full bg-white border border-[#D2D2D7] text-[#86868B] hover:text-[#1D1D1F] transition">
                      <X size={15} />
                    </button>
                  </div>
                ) : (
                  <label
                    onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); setAvDragActive(true) }}
                    onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setAvDragActive(true) }}
                    onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setAvDragActive(false) }}
                    onDrop={(e) => {
                      e.preventDefault(); e.stopPropagation(); setAvDragActive(false)
                      if (e.dataTransfer.files?.[0]) processAvatarFile(e.dataTransfer.files[0])
                    }}
                    className={`flex flex-col items-center justify-center gap-1.5 py-6 rounded-xl border-2 border-dashed cursor-pointer transition-colors
                      ${avDragActive ? 'border-[#0071E3] bg-[#0071E3]/[0.04]' : 'border-[#D2D2D7] hover:border-[#0071E3]/50'}`}
                  >
                    <ImagePlus size={18} className="text-[#86868B]" />
                    <p className="text-[12px] text-[#86868B]">Drag an image here, or click to choose</p>
                    <input type="file" accept="image/*" className="hidden"
                      onChange={(e) => { if (e.target.files?.[0]) processAvatarFile(e.target.files[0]) }} />
                  </label>
                )}

                {avUploadError && <p className="text-[12px] text-[#D70015]">{avUploadError}</p>}

                {avUploadPreview && (
                  <button onClick={submitAvatarUpload} disabled={avatarUploadMutation.isPending}
                    className="w-full py-2.5 rounded-full text-[13px] font-semibold text-white bg-[#0071E3] hover:bg-[#0077ED] disabled:opacity-50 transition flex items-center justify-center gap-2 active:scale-[0.98]">
                    {avatarUploadMutation.isPending
                      ? (<><Loader2 size={14} className="animate-spin" /> Uploading…</>)
                      : (<><Upload size={14} /> Add presenter</>)}
                  </button>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-4 sm:grid-cols-5 gap-2.5">
                {readyAvatars.map((av) => {
                  const selected = av.id === avatarId
                  return (
                    <button
                      key={av.id}
                      onClick={() => { setAvatarId(av.id); setImageUrl((av.image_url || av.thumbnail_url) as string) }}
                      className={`relative rounded-2xl overflow-hidden aspect-square transition-all duration-200
                        ${selected ? 'ring-[3px] ring-[#0071E3] ring-offset-2 ring-offset-white' : 'ring-1 ring-[#E5E5EA] hover:ring-[#0071E3]/40'}`}
                    >
                      <img src={(av.thumbnail_url || av.image_url) as string} alt={av.name}
                        className="w-full h-full object-cover" />
                      {selected && (
                        <div className="absolute top-1 right-1 w-5 h-5 rounded-full bg-[#0071E3] flex items-center justify-center shadow">
                          <Check size={13} className="text-white" strokeWidth={3} />
                        </div>
                      )}
                    </button>
                  )
                })}
              </div>
            )}

            <div className="h-px bg-[#E5E5EA] my-6" />

            {/* Optional: voice cloning */}
            <div className="flex items-start justify-between gap-3 mb-1">
              <div className="flex items-baseline gap-2.5">
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-[#0071E3] text-white text-[13px] font-semibold flex-shrink-0">
                  <Mic size={13} />
                </span>
                <div>
                  <h2 className="text-[17px] font-semibold text-[#1D1D1F] leading-tight">Clone a voice</h2>
                  <p className="text-[13px] text-[#86868B]">Optional — off uses the default voice</p>
                </div>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={cloneOn}
                onClick={() => setCloneOn((v) => !v)}
                className={`relative w-[46px] h-[28px] rounded-full transition-colors duration-200 flex-shrink-0 mt-0.5 ${cloneOn ? 'bg-[#30D158]' : 'bg-[#E5E5EA]'}`}
              >
                <span className={`absolute top-[3px] left-[3px] w-[22px] h-[22px] rounded-full bg-white shadow transition-transform duration-200 ${cloneOn ? 'translate-x-[18px]' : ''}`} />
              </button>
            </div>

            {cloneOn && (
              <div className="mt-4 rounded-2xl bg-[#FAFAFC] border border-[#E5E5EA] p-4 space-y-3">
                <div className="flex gap-1 p-1 rounded-xl bg-[#EDEDF0]">
                  <button type="button" onClick={() => setVoiceMode('pick')}
                    className={`flex-1 py-1.5 rounded-lg text-[13px] font-medium transition ${voiceMode === 'pick' ? 'bg-white text-[#1D1D1F] shadow-sm' : 'text-[#86868B]'}`}>
                    Saved voices
                  </button>
                  <button type="button" onClick={() => setVoiceMode('upload')}
                    className={`flex-1 py-1.5 rounded-lg text-[13px] font-medium transition ${voiceMode === 'upload' ? 'bg-white text-[#1D1D1F] shadow-sm' : 'text-[#86868B]'}`}>
                    Upload new
                  </button>
                </div>

                {voiceMode === 'pick' ? (
                  (voices ?? []).length === 0 ? (
                    <p className="text-[13px] text-[#86868B] px-1 py-2">
                      No saved voices yet — switch to “Upload new” to add one.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {(voices ?? []).map((v) => {
                        const sel = v.id === voiceId
                        return (
                          <button key={v.id} type="button" onClick={() => setVoiceId(v.id)}
                            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl border text-left transition ${sel ? 'border-[#0071E3] bg-[#0071E3]/[0.05]' : 'border-[#E5E5EA] bg-white hover:border-[#0071E3]/40'}`}>
                            <span className={`flex items-center justify-center w-8 h-8 rounded-full flex-shrink-0 ${sel ? 'bg-[#0071E3] text-white' : 'bg-[#F0F0F2] text-[#86868B]'}`}>
                              <AudioLines size={15} />
                            </span>
                            <span className="flex-1 min-w-0">
                              <span className="block text-[14px] font-medium text-[#1D1D1F] truncate">{v.name}</span>
                              <span className="block text-[12px] text-[#86868B]">{v.language?.toUpperCase()} · {Math.round(v.duration)}s</span>
                            </span>
                            {sel && <Check size={16} className="text-[#0071E3] flex-shrink-0" strokeWidth={3} />}
                          </button>
                        )
                      })}
                    </div>
                  )
                ) : (
                  <div className="space-y-3">
                    <label className="flex flex-col items-center justify-center gap-1.5 py-5 rounded-xl border-2 border-dashed border-[#D2D2D7] hover:border-[#0071E3]/50 cursor-pointer transition-colors">
                      <AudioLines size={18} className="text-[#86868B]" />
                      <p className="text-[12px] text-[#86868B]">
                        {voiceFile ? voiceFile.name : 'Choose a 10–60s clip (WAV, MP3, WEBM)'}
                      </p>
                      <input type="file" accept="audio/*,.wav,.mp3,.webm,.m4a,.ogg" className="hidden"
                        onChange={(e) => { if (e.target.files?.[0]) pickVoiceFile(e.target.files[0]) }} />
                    </label>
                    {voiceFile && (
                      <input type="text" value={voiceName} onChange={(e) => setVoiceName(e.target.value)}
                        placeholder="Name this voice"
                        className="w-full px-3.5 py-2.5 rounded-xl bg-white border border-[#D2D2D7] text-[14px] text-[#1D1D1F] placeholder:text-[#AEAEB2] focus:outline-none focus:ring-4 focus:ring-[#0071E3]/15 focus:border-[#0071E3] transition" />
                    )}
                    <button type="button" onClick={submitClone} disabled={cloneMutation.isPending || !voiceFile}
                      className="w-full py-2.5 rounded-full text-[13px] font-semibold text-white bg-[#0071E3] hover:bg-[#0077ED] disabled:opacity-50 transition flex items-center justify-center gap-2 active:scale-[0.98]">
                      {cloneMutation.isPending
                        ? (<><Loader2 size={14} className="animate-spin" /> Cloning…</>)
                        : (<><Mic size={14} /> Clone this voice</>)}
                    </button>
                  </div>
                )}

                {voiceErr && <p className="text-[12px] text-[#D70015] px-1">{voiceErr}</p>}
                {voiceId && voiceMode === 'pick' && (
                  <p className="text-[12px] text-[#1D7A3E] px-1">Voice selected — clips will use this cloned voice.</p>
                )}
              </div>
            )}

            <div className="h-px bg-[#E5E5EA] my-6" />

            {/* Step 4: language */}
            <div className="flex items-baseline gap-2.5 mb-4">
              <span className="flex items-center justify-center w-6 h-6 rounded-full bg-[#0071E3] text-white text-[13px] font-semibold flex-shrink-0">4</span>
              <h2 className="text-[17px] font-semibold text-[#1D1D1F] leading-tight">Language</h2>
            </div>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}
              className="w-full px-4 py-3 rounded-xl bg-white border border-[#D2D2D7] text-[15px] text-[#1D1D1F] focus:outline-none focus:ring-4 focus:ring-[#0071E3]/15 focus:border-[#0071E3] transition">
              {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
            </select>

            {error && (
              <div className="mt-5 flex items-center gap-2 px-3.5 py-3 rounded-xl bg-[#FFF2F2] border border-[#FFD4D4]">
                <AlertCircle size={15} className="text-[#D70015] flex-shrink-0" />
                <p className="text-[13px] text-[#D70015]">{error}</p>
              </div>
            )}

            <button
              onClick={startJob}
              disabled={!mediaPath || !imageUrl || isBusy}
              className="mt-6 w-full py-3.5 text-[16px] font-semibold rounded-full text-white bg-[#0071E3] hover:bg-[#0077ED] disabled:opacity-40 disabled:cursor-not-allowed transition-all active:scale-[0.99] flex items-center justify-center gap-2"
            >
              {isBusy
                ? (<><Loader2 size={18} className="animate-spin" /> Generating…</>)
                : (<><Play size={17} fill="white" /> Generate video</>)}
            </button>
          </div>

          {/* ── RIGHT: output ── */}
          <div className="bg-white rounded-[20px] border border-[#EDEDF0] p-7 min-h-[520px] flex flex-col"
               style={{ boxShadow: '0 4px 24px rgba(0,0,0,0.06)' }}>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-[17px] font-semibold text-[#1D1D1F]">Your video</h2>
              {job && (
                <button onClick={resetAll}
                  className="text-[13px] font-medium text-[#0071E3] hover:text-[#0077ED] transition-colors">
                  Start over
                </button>
              )}
            </div>

            {/* Empty state */}
            {!showRun && (
              <div className="flex-1 flex flex-col items-center justify-center text-center py-10">
                <div className="w-16 h-16 rounded-2xl bg-[#F5F5F7] flex items-center justify-center mb-4">
                  <Film size={28} className="text-[#C7C7CC]" />
                </div>
                <p className="text-[15px] font-medium text-[#1D1D1F]">Nothing generated yet</p>
                <p className="text-[13px] text-[#86868B] mt-1 max-w-xs">
                  Complete the three steps and press Generate. You’ll watch each stage run here.
                </p>
              </div>
            )}

            {/* Running — the live stepper */}
            {showRun && !isDone && (
              <div className="flex-1">
                {/* progress bar */}
                <div className="mb-6">
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="text-[13px] font-medium text-[#6E6E73]">
                      {isFailed ? 'Stopped' : 'Working…'}
                    </span>
                    <span className="text-[13px] font-semibold tabular-nums text-[#1D1D1F]">{progress}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-[#E5E5EA] overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-500 ${isFailed ? 'bg-[#D70015]' : 'bg-[#0071E3]'}`}
                      style={{ width: `${Math.max(progress, 4)}%` }} />
                  </div>
                </div>

                {/* stages */}
                <ol className="space-y-1">
                  {STAGES.map((s) => {
                    const st = stageStatus(s.from, s.to, progress, jobStatus)
                    return (
                      <li key={s.key} className="flex items-start gap-3 py-2.5">
                        <span className={`mt-0.5 flex items-center justify-center w-6 h-6 rounded-full flex-shrink-0 transition-all duration-300
                          ${st === 'done' ? 'bg-[#30D158] text-white'
                            : st === 'active' ? 'bg-[#0071E3] text-white'
                            : st === 'error' ? 'bg-[#D70015] text-white'
                            : 'bg-[#F0F0F2] text-[#C7C7CC]'}`}>
                          {st === 'done' ? <Check size={14} strokeWidth={3} />
                            : st === 'active' ? <Loader2 size={13} className="animate-spin" />
                            : st === 'error' ? <X size={14} strokeWidth={3} />
                            : <span className="w-1.5 h-1.5 rounded-full bg-current" />}
                        </span>
                        <div className="min-w-0">
                          <p className={`text-[15px] font-medium leading-tight
                            ${st === 'idle' ? 'text-[#AEAEB2]' : 'text-[#1D1D1F]'}`}>
                            {s.label}
                          </p>
                          <p className={`text-[13px] mt-0.5 ${st === 'idle' ? 'text-[#C7C7CC]' : 'text-[#86868B]'}`}>
                            {s.detail}
                          </p>
                        </div>
                      </li>
                    )
                  })}
                </ol>

                {isFailed && job?.error && (
                  <div className="mt-4 px-3.5 py-3 rounded-xl bg-[#FFF2F2] border border-[#FFD4D4]">
                    <p className="text-[13px] text-[#D70015] break-words">{job.error}</p>
                  </div>
                )}
              </div>
            )}

            {/* Done — clips */}
            {isDone && (
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-5">
                  <div className="w-6 h-6 rounded-full bg-[#30D158] flex items-center justify-center">
                    <Check size={14} className="text-white" strokeWidth={3} />
                  </div>
                  <p className="text-[15px] font-semibold text-[#1D1D1F]">
                    {clips.length} clip{clips.length === 1 ? '' : 's'} ready
                  </p>
                </div>

                {clips.length === 0 && (
                  <p className="text-[13px] text-[#86868B]">No clips were produced for this run.</p>
                )}

                <div className="space-y-4">
                  {clips.map((clip) => {
                    const obj = clipUrls[clip.index]
                    return (
                      <div key={clip.index} className="rounded-2xl border border-[#E5E5EA] overflow-hidden bg-[#FAFAFC]">
                        {obj ? (
                          <video src={obj} controls className="w-full bg-black aspect-video" />
                        ) : (
                          <div className="w-full aspect-video flex items-center justify-center bg-[#F0F0F2]">
                            <Loader2 size={20} className="animate-spin text-[#AEAEB2]" />
                          </div>
                        )}
                        <div className="flex items-center justify-between gap-3 px-4 py-3">
                          <div className="min-w-0">
                            <p className="text-[14px] font-medium text-[#1D1D1F] truncate">{clip.title || `Clip ${clip.index + 1}`}</p>
                            {clip.engine && <p className="text-[12px] text-[#86868B]">Rendered with {clip.engine}</p>}
                          </div>
                          {obj && (
                            <button onClick={() => downloadClip(clip, obj)}
                              className="flex items-center gap-1.5 px-3.5 py-2 rounded-full text-[13px] font-semibold text-[#0071E3] hover:bg-[#0071E3]/[0.08] transition flex-shrink-0">
                              <Download size={14} /> Download
                            </button>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
