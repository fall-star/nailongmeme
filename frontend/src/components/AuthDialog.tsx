import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import type { AuthMode, AuthUser } from '../lib/auth-client';

type Props = {
  open: boolean;
  mode: AuthMode;
  onClose: () => void;
  onAuth: (user: AuthUser) => void;
};

export default function AuthDialog({ open, mode: initialMode, onClose, onAuth }: Props) {
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [nickname, setNickname] = useState('');
  const [email, setEmail] = useState('');
  const [confirm, setConfirm] = useState('');
  const [avatar, setAvatar] = useState<File | null>(null);
  const [code, setCode] = useState('');
  const [challengeId, setChallengeId] = useState('');
  const [maskedEmail, setMaskedEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setError('');
    setNotice('');
    setPassword('');
    setConfirm('');
    setCode('');
    setChallengeId('');
    setMaskedEmail('');
    window.setTimeout(() => firstInputRef.current?.focus(), 80);
  }, [open, initialMode]);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [open, onClose]);

  if (!open) return null;

  function chooseAvatar(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    if (file && file.size > 5 * 1024 * 1024) {
      setError('头像不能超过 5 MB');
      setAvatar(null);
      return;
    }
    setAvatar(file);
    setError('');
  }

  async function login(event: FormEvent) {
    event.preventDefault();
    setError('');
    if (!identifier.trim() || !password) { setError('请输入昵称/邮箱和密码'); return; }
    setBusy(true);
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier: identifier.trim(), password }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || '登录失败');
      if (result.requires_email_code) {
        setChallengeId(result.challenge_id);
        setMaskedEmail(result.masked_email || '');
        setCode('');
        setNotice('管理员登录需要邮箱验证码，验证码已发送。');
        return;
      }
      onAuth(result.user);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode(event: FormEvent) {
    event.preventDefault();
    setError('');
    if (!/^\d{6}$/.test(code.trim())) { setError('请输入 6 位数字验证码'); return; }
    setBusy(true);
    try {
      const response = await fetch('/api/auth/verify-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge_id: challengeId, code: code.trim() }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || '验证码错误');
      onAuth(result.user);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : '验证码错误');
    } finally {
      setBusy(false);
    }
  }

  async function resendCode() {
    setError('');
    setBusy(true);
    try {
      const response = await fetch('/api/auth/resend-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ challenge_id: challengeId }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || '发送失败');
      setNotice('新的验证码已发送。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '发送失败');
    } finally {
      setBusy(false);
    }
  }

  async function register(event: FormEvent) {
    event.preventDefault();
    setError('');
    if (!nickname.trim() || !email.trim() || !password) { setError('请填写昵称、邮箱和密码'); return; }
    if (password.length < 6) { setError('密码至少需要 6 位'); return; }
    if (password !== confirm) { setError('两次输入的密码不一致'); return; }
    setBusy(true);
    try {
      const response = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nickname: nickname.trim(), email: email.trim(), password }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || '注册失败');
      if (avatar) {
        const upload = await fetch('/api/account/avatar', {
          method: 'POST',
          headers: { 'Content-Type': avatar.type || 'application/octet-stream' },
          body: avatar,
        });
        if (upload.ok) {
          const avatarResult = await upload.json().catch(() => ({}));
          if (avatarResult.avatar_url) result.user.avatar_url = avatarResult.avatar_url;
        }
      }
      onAuth(result.user);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败');
    } finally {
      setBusy(false);
    }
  }

  function switchMode(next: AuthMode) {
    setMode(next);
    setError('');
    setNotice('');
    setPassword('');
    setConfirm('');
    setCode('');
    setChallengeId('');
    setMaskedEmail('');
  }

  return (
    <div className="auth-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={dialogRef} className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <div className="auth-head">
          <div>
            <div className="eyebrow"><span className="dot" /> MEMBER ACCOUNT</div>
            <h2 id="auth-title">{challengeId ? '验证管理员邮箱' : mode === 'login' ? '欢迎回来' : '加入表情库'}</h2>
            <p>{challengeId ? `验证码已发送到 ${maskedEmail}` : mode === 'login' ? '登录后即可投稿和给图片补充标签。' : '注册后可以投稿、打 tag，并管理自己的头像。'}</p>
          </div>
          <button className="auth-close" type="button" onClick={onClose} aria-label="关闭">×</button>
        </div>

        {challengeId ? (
          <form className="auth-form" onSubmit={verifyCode}>
            <label className="auth-field"><span>邮箱验证码</span><input ref={firstInputRef} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))} placeholder="6 位数字验证码" /></label>
            {notice && <div className="auth-notice">{notice}</div>}
            {error && <div className="auth-error">{error}</div>}
            <div className="auth-actions">
              <button className="btn btn-ghost" type="button" onClick={() => { setChallengeId(''); setNotice(''); setError(''); }}>返回</button>
              <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? '验证中…' : '验证并登录 →'}</button>
            </div>
            <button className="auth-resend" type="button" onClick={resendCode} disabled={busy}>没收到？重新发送验证码</button>
          </form>
        ) : (
          <>
            <div className="auth-tabs" role="tablist">
              <button className={mode === 'login' ? 'active' : ''} type="button" onClick={() => switchMode('login')}>登录</button>
              <button className={mode === 'register' ? 'active' : ''} type="button" onClick={() => switchMode('register')}>注册</button>
            </div>

            {mode === 'login' ? (
              <form className="auth-form" onSubmit={login}>
                <label className="auth-field"><span>昵称或邮箱</span><input ref={firstInputRef} autoComplete="username" value={identifier} onChange={(event) => setIdentifier(event.target.value)} placeholder="请输入昵称或邮箱" /></label>
                <label className="auth-field"><span>密码</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="输入密码" /></label>
                {error && <div className="auth-error">{error}</div>}
                <div className="auth-actions">
                  <button className="btn btn-ghost" type="button" onClick={onClose}>取消</button>
                  <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? '登录中…' : '登录 →'}</button>
                </div>
              </form>
            ) : (
              <form className="auth-form" onSubmit={register}>
                <div className="auth-two">
                  <label className="auth-field"><span>昵称</span><input ref={firstInputRef} autoComplete="nickname" maxLength={24} value={nickname} onChange={(event) => setNickname(event.target.value)} placeholder="2–24 个字符" /></label>
                  <label className="auth-field"><span>邮箱</span><input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label>
                </div>
                <div className="auth-two">
                  <label className="auth-field"><span>密码</span><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 6 位" /></label>
                  <label className="auth-field"><span>确认密码</span><input type="password" autoComplete="new-password" value={confirm} onChange={(event) => setConfirm(event.target.value)} placeholder="再输一次" /></label>
                </div>
                <label className="auth-avatar-field">
                  <span>头像 <i>可选</i></span>
                  <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={chooseAvatar} />
                  <b>{avatar ? avatar.name : '选择一张图片，不上传则使用默认头像'}</b>
                </label>
                {error && <div className="auth-error">{error}</div>}
                <div className="auth-actions">
                  <button className="btn btn-ghost" type="button" onClick={onClose}>取消</button>
                  <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? '创建中…' : '创建账号 →'}</button>
                </div>
              </form>
            )}
          </>
        )}
      </section>
    </div>
  );
}
