import { useState } from 'react';
import type { FormEvent } from 'react';
import { getCurrentUser, openAuth } from '../lib/auth-client';

type Props = { imageId: string; imageName: string; existingTags: string[] };

export default function TagSuggest({ imageId, imageName, existingTags }: Props) {
  const [open, setOpen] = useState(false);
  const [tags, setTags] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function toggleOpen() {
    if (!open) {
      const current = await getCurrentUser();
      if (!current) {
        openAuth('login');
        return;
      }
    }
    setOpen((value) => !value);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    const values = [...new Set(tags.split(/[,，]/).map((value) => value.trim()).filter(Boolean))];
    if (!values.length) { setError('请至少填写一个标签'); return; }
    if (values.some((value) => existingTags.includes(value))) { setError('这个标签已经存在，换一个吧'); return; }
    setBusy(true);
    try {
      const response = await fetch('/api/tag-suggestions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: imageId, tags: values, note: note.trim() }),
      });
      const result = await response.json().catch(() => ({}));
      if (response.status === 401) {
        setOpen(false);
        openAuth('login');
        throw new Error('请先登录后再提交标签');
      }
      if (!response.ok) throw new Error(result.detail || '提交失败');
      setMessage('tag 已提交，管理员审核通过后才会生效。');
      setTags('');
      setNote('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={open ? 'tag-suggest is-open' : 'tag-suggest'}>
      <button className="tag-suggest-trigger" type="button" onClick={toggleOpen} aria-expanded={open}>
        <span className="tag-suggest-icon">#</span>
        <span><b>我要打tag</b><small>给「{imageName}」补充标签</small></span>
        <span className="tag-suggest-arrow">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <form className="tag-suggest-form" onSubmit={submit}>
          <div className="tag-suggest-note">提交后会进入管理员审核，不会立即修改这张图的标签。</div>
          <label><span>建议标签</span><input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="奶蛙, 大笑, 上班" maxLength={300} autoFocus /></label>
          <label><span>备注 <i>可选</i></span><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="为什么适合这个标签？" maxLength={300} /></label>
          {error && <div className="tag-suggest-error">{error}</div>}
          {message && <div className="tag-suggest-success">{message}</div>}
          <div className="tag-suggest-actions">
            <button className="btn btn-sm" type="button" onClick={() => setOpen(false)}>收起</button>
            <button className="btn btn-sm btn-primary" type="submit" disabled={busy}>{busy ? '提交中…' : '提交给管理员 →'}</button>
          </div>
        </form>
      )}
    </div>
  );
}
