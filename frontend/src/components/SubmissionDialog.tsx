import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';

type Props = {
  open: boolean;
  onClose: () => void;
};

export default function SubmissionDialog({ open, onClose }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState('');
  const [name, setName] = useState('');
  const [tags, setTags] = useState('');
  const [source, setSource] = useState('');
  const [contact, setContact] = useState('');
  const [rights, setRights] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setMessage('');
    setError('');
    window.setTimeout(() => inputRef.current?.focus(), 80);
  }, [open]);

  useEffect(() => {
    if (!file) { setPreview(''); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  if (!open) return null;

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
    setError('');
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    if (!file) { setError('请先选择一张图片'); return; }
    if (!rights) { setError('请确认拥有投稿和展示权限'); return; }
    if (file.size > 25 * 1024 * 1024) { setError('图片不能超过 25 MB'); return; }
    setBusy(true);
    try {
      const params = new URLSearchParams({
        filename: file.name,
        name: name.trim(),
        tag: tags.trim(),
        source: source.trim(),
        contact: contact.trim(),
        rights_accepted: 'true',
      });
      const response = await fetch(`/api/submissions?${params}`, {
        method: 'POST',
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || '投稿失败');
      setMessage('投稿已收到，管理员审核通过后才会公开。');
      setFile(null);
      setName('');
      setTags('');
      setSource('');
      setContact('');
      setRights(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '投稿失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="submit-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="submit-dialog" role="dialog" aria-modal="true" aria-labelledby="submit-title">
        <div className="submit-head">
          <div>
            <div className="eyebrow"><span className="dot" /> COMMUNITY SUBMISSION</div>
            <h2 id="submit-title">我也要投稿</h2>
            <p>投一张你觉得好用的奶龙表情。图片会进入隔离审核区，通过后才会公开。</p>
          </div>
          <button className="submit-close" type="button" onClick={onClose} aria-label="关闭">×</button>
        </div>

        {message ? (
          <div className="submit-success">
            <div className="success-mark">✓</div>
            <h3>已经收到</h3>
            <p>{message}</p>
            <button className="btn btn-yellow" type="button" onClick={onClose}>知道了</button>
          </div>
        ) : (
          <form className="submit-form" onSubmit={submit}>
            <label className={preview ? 'submit-file has-preview' : 'submit-file'}>
              <input ref={inputRef} type="file" accept="image/jpeg,image/png,image/gif,image/webp,image/avif,image/heic" onChange={chooseFile} />
              {preview ? <img src={preview} alt="投稿预览" /> : <><span className="file-plus">＋</span><b>选择一张表情图片</b><small>JPG / PNG / GIF / WebP，最大 25 MB</small></>}
            </label>
            {file && <div className="submit-file-name">{file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB</div>}

            <div className="submit-grid">
              <label className="submit-field"><span>名称 <i>可选</i></span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="比如：今天不想上班" maxLength={120} /></label>
              <label className="submit-field"><span>标签 <i>可选，用逗号分隔</i></span><input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="奶蛙,大笑,摆烂" maxLength={300} /></label>
              <label className="submit-field"><span>来源 <i>可选</i></span><input value={source} onChange={(event) => setSource(event.target.value)} placeholder="原创 / 出处链接 / 作者" maxLength={500} /></label>
              <label className="submit-field"><span>联系方式 <i>可选，不公开</i></span><input value={contact} onChange={(event) => setContact(event.target.value)} placeholder="邮箱或社交账号" maxLength={300} /></label>
            </div>

            <label className="submit-rights">
              <input type="checkbox" checked={rights} onChange={(event) => setRights(event.target.checked)} />
              <span>我确认拥有这张图片的投稿和展示权限，并同意管理员审核后公开。</span>
            </label>
            {error && <div className="submit-error">{error}</div>}
            <div className="submit-actions">
              <button className="btn" type="button" onClick={onClose}>取消</button>
              <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? '上传中…' : '提交审核 →'}</button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
