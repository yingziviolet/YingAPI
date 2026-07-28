import { useState } from 'react'
import { ArrowRight, CheckCircle2, Copy, Loader2, Sparkles, Terminal } from 'lucide-react'
import { api } from '../api'
import { Logo } from '../components/TitleBar'
import { Button, Hint, Input } from '../components/ui'

const PRESETS = [
  { label: 'DeepSeek', base: 'https://api.deepseek.com/v1', hint: 'sk- 开头' },
  { label: 'Kimi / Moonshot', base: 'https://api.moonshot.cn/v1', hint: 'sk- 开头' },
  { label: '智谱 GLM', base: 'https://open.bigmodel.cn/api/paas/v4', hint: 'xxx.yyy 格式' },
  { label: '硅基流动', base: 'https://api.siliconflow.cn/v1', hint: 'sk- 开头' },
]

export default function Setup({ onDone }: { onDone: () => void }) {
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [models, setModels] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<any>(null)
  const [copied, setCopied] = useState('')

  async function start() {
    setError('')
    setBusy(true)
    try {
      const body: Record<string, unknown> = { api_key: apiKey.trim() }
      if (baseUrl.trim()) body.base_url = baseUrl.trim()
      const list = models.split(',').map((s) => s.trim()).filter(Boolean)
      if (list.length) body.models = list
      setResult(await api.quickstart(body))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function copy(text: string, tag: string) {
    navigator.clipboard.writeText(text)
    setCopied(tag)
    setTimeout(() => setCopied(''), 1600)
  }

  if (result) {
    const openaiSnippet =
      `export OPENAI_BASE_URL=${result.base_url}\n` + `export OPENAI_API_KEY=${result.key.key}`
    const anthropicSnippet =
      `export ANTHROPIC_BASE_URL=${result.anthropic_base_url}\n` +
      `export ANTHROPIC_AUTH_TOKEN=${result.key.key}`
    return (
      <div className="mx-auto max-w-[720px] py-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-good/12">
            <CheckCircle2 size={24} className="text-good" />
          </div>
          <div>
            <h1 className="text-[22px] font-bold tracking-tight text-ink-hi">配置完成,可以用了</h1>
            <p className="mt-0.5 text-[13px] text-ink-mid">
              渠道「{result.channel.name}」已就绪
              {result.balance?.remaining != null &&
                ` · 余额 ${result.balance.currency === 'CNY' ? '¥' : '$'}${Number(result.balance.remaining).toFixed(2)}`}
            </p>
          </div>
        </div>

        <div className="card p-6">
          <div className="mb-1 flex items-center gap-2 text-[14px] font-semibold text-ink-hi">
            <Terminal size={16} className="text-brand" />
            把这段配到你的客户端
          </div>
          <p className="mb-4 text-[12.5px] text-ink-mid">
            下面的 key 是网关发给你的虚拟 key(不是你上面填的真 key)。真 key 已加密存好,永远不会再显示。
          </p>

          <SnippetBlock
            title="OpenAI 兼容客户端(IDE 插件 / 脚本 / 各类 agent)"
            code={openaiSnippet}
            copied={copied === 'openai'}
            onCopy={() => copy(openaiSnippet, 'openai')}
          />
          <div className="h-3" />
          <SnippetBlock
            title="Anthropic 协议客户端(Claude Code 等)"
            code={anthropicSnippet}
            copied={copied === 'anthropic'}
            onCopy={() => copy(anthropicSnippet, 'anthropic')}
          />

          <div className="mt-5 border-t border-line-soft pt-4">
            <Button kind="primary" onClick={onDone}>
              进入控制台<ArrowRight size={14} />
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[620px] py-10">
      <div className="mb-7 flex flex-col items-center text-center">
        <Logo size={52} />
        <h1 className="mt-4 text-[24px] font-bold tracking-tight text-ink-hi">欢迎使用 LLM 网关</h1>
        <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-mid">
          填一个你自己的 API key,30 秒完成配置。
          <br />
          之后所有 LLM 调用都能走网关:自动缓存省钱、渠道故障自动切换、每一分花费都看得见。
        </p>
      </div>

      <div className="card p-6">
        <label className="block">
          <span className="mb-1.5 flex items-center gap-1.5 text-[13px] font-medium text-ink">
            <Sparkles size={14} className="text-brand" />
            你的 API Key
          </span>
          <Input
            type="password"
            placeholder="粘贴 API key,自动识别是哪家厂商"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full"
            autoFocus
          />
        </label>

        <div className="mt-3 flex flex-wrap gap-1.5">
          <span className="py-1 text-[11.5px] text-ink-low">常用厂商(点击填入地址):</span>
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => setBaseUrl(p.base)}
              className={`rounded-md border px-2 py-1 text-[11.5px] transition-colors ${
                baseUrl === p.base
                  ? 'border-brand-ring bg-brand-soft text-brand'
                  : 'border-line bg-surface-sunken text-ink-mid hover:text-ink-hi'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        <details className="mt-4 rounded-lg border border-line bg-surface-sunken px-4 py-3">
          <summary className="cursor-pointer text-[12.5px] font-medium text-ink">
            识别不出厂商?手动填(用中转站也在这里填)
          </summary>
          <div className="mt-3 space-y-3">
            <label className="block">
              <span className="mb-1 block text-[12px] text-ink-mid">Base URL</span>
              <Input
                placeholder="https://api.xxx.com/v1"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                className="w-full"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[12px] text-ink-mid">模型名(逗号分隔)</span>
              <Input
                placeholder="deepseek-chat, deepseek-reasoner"
                value={models}
                onChange={(e) => setModels(e.target.value)}
                className="w-full"
              />
            </label>
          </div>
        </details>

        {error && (
          <div className="mt-3 rounded-lg border border-alert/25 bg-alert/8 px-3 py-2 text-[12.5px] text-alert">
            {error}
          </div>
        )}

        <div className="mt-5">
          <Button kind="primary" onClick={start} disabled={!apiKey.trim() || busy}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
            {busy ? '正在验证并配置…' : '开始使用'}
          </Button>
        </div>

        <p className="mt-4 border-t border-line-soft pt-4 text-[11.5px] leading-relaxed text-ink-low">
          key 会用 Fernet 加密存在本机(<code className="rounded bg-surface-sunken px-1">%LOCALAPPDATA%\LLMGateway</code>),
          不上传任何地方。网关只管理你自己合法持有的 key。
        </p>
      </div>

      <div className="mt-4">
        <Hint>
          还没有 API key?去 DeepSeek / 硅基流动这类平台注册,充值几块钱就能拿到,
          配合网关的缓存与降级,够用很久。
        </Hint>
      </div>

      <div className="mt-4 text-center">
        <button onClick={onDone} className="text-[12px] text-ink-low hover:text-ink-mid">
          跳过,我自己配置
        </button>
      </div>
    </div>
  )
}

function SnippetBlock({ title, code, copied, onCopy }: {
  title: string
  code: string
  copied: boolean
  onCopy: () => void
}) {
  return (
    <div className="rounded-lg border border-line bg-surface-sunken">
      <div className="flex items-center justify-between border-b border-line-soft px-3 py-2">
        <span className="text-[12px] font-medium text-ink">{title}</span>
        <Button kind="ghost" onClick={onCopy}>
          <Copy size={13} />
          {copied ? '已复制' : '复制'}
        </Button>
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 font-mono text-[11.5px] leading-relaxed text-ink-hi">
        {code}
      </pre>
    </div>
  )
}
