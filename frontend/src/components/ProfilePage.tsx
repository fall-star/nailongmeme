import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { getCurrentUser, openAuth } from '../lib/auth-client';
import type { AuthUser } from '../lib/auth-client';

const MAX_AVATAR = 5 * 1024 * 1024;

export default function ProfilePage() {
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined);
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarPreview, setAvatarPreview] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [busy, setBusy] = useState<'avatar' | 'password' | 'logout' | ''>('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser().then((value) => { if (!cancelled) setUser(value); });
    function onAuthChanged(event: Event) {
      const detail = (event as CustomEvent<{ user?: AuthUser | null }>).detail;
      if (detail && 'user' in detail) setUser(detail.user ?? null);
    }
    window.addEventListener('auth-changed', onAuthChanged);
    return () => {
      cancelled = true;
      window.removeEventListener('auth-changed', onAuthChanged);
    };
  }, []);

  useEffect(() => () => { if (avatarPreview) URL.revokeObjectURL(avatarPreview); }, [avatarPreview]);

  function remember(next: AuthUser) {
    setUser(next);
    window.dispatchEvent(new CustomEvent('auth-changed', { detail: { user: next } }));
  }

  function chooseAvatar(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setError('');
    setNotice('');
    if (!file) return;
    if (!file.type.startsWith('image/')) { setError('头像必须是图片'); return; }
    if (file.size > MAX_AVATAR) { setError('头像不能超过 5 MB'); return; }
    if (avatarPreview) URL.revokeObjectURL(avatarPreview);
    setAvatarFile(file);
    setAvatarPreview(URL.createObjectURL(file));
  }

  async function saveAvatar() {
    if (!avatarFile) { setError('请先选择一张头像'); return; }
    setBusy('avatar');
    setError('');
    setNotice('');
    try {
      const response = await fetch('/api/account/avatar', {
        method: 'POST',
        headers: { 'Content-Type': avatarFile.type || 'application/octet-stream' },
        body: avatarFile,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || '头像上传失败');
      if (!user) return;
      const next = { ...user, avatar_url: `${result.avatar_url}?v=${Date.now()}` };
      remember(next);
      setAvatarFile(null);
      setNotice('头像已更新。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '头像上传失败');
    } finally {
      setBusy('');
    }
  }

  async function savePassword(event: FormEvent) {
    event.preventDefault();
    if (newPassword.length < 6) { setError('新密码至少需要 6 位'); return; }
    setBusy('password');
    setError('');
    setNotice('');
    try {
      const response = await fetch('/api/account/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: newPassword }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || '修改密码失败');
      setNewPassword('');
      setNotice('密码已修改；其他设备上的登录已退出。');
    } catch (err) {
      setError(err instanceof Error ? err.message : '修改密码失败');
    } finally {
      setBusy('');
    }
  }

  async function logout() {
    setBusy('logout');
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/';
  }

  if (user === undefined) {
    return <main className="profile-page shell"><div className="profile-loading">正在读取账户信息…</div></main>;
  }

  if (user === null) {
    return (
      <main className="profile-page shell">
        <div className="profile-gate">
          <div className="eyebrow"><span className="dot" /> MEMBER ACCOUNT</div>
          <h1>先登录，再管理账户</h1>
          <p>登录后可以换头像、修改密码和退出登录。</p>
          <div className="profile-gate-actions">
            <button className="btn btn-primary" type="button" onClick={() => openAuth('login')}>登录</button>
            <button className="btn" type="button" onClick={() => openAuth('register')}>注册</button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="profile-page shell">
      <div className="profile-heading">
        <div>
          <div className="eyebrow"><span className="dot" /> MEMBER ACCOUNT</div>
          <h1>个人信息<span className="title-dot">.</span></h1>
          <p>管理你的头像和登录密码。</p>
        </div>
        <a className="profile-back" href="/">← 回到表情库</a>
      </div>

      <section className="profile-card">
        <div className="profile-avatar-wrap">
          <img className="profile-avatar" src={avatarPreview || user.avatar_url} alt={`${user.nickname} 的头像`} />
          <button className="profile-avatar-edit" type="button" onClick={() => fileRef.current?.click()}>更换头像</button>
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={chooseAvatar} hidden />
        </div>
        <div className="profile-identity">
          <h2>{user.nickname}</h2>
          <p>{user.email}</p>
          <span className="profile-role">{user.is_admin ? '管理员' : '普通用户'}</span>
        </div>
        <div className="profile-avatar-actions">
          {avatarFile && <span className="profile-picked">{avatarFile.name}</span>}
          <button className="btn btn-primary btn-sm" type="button" disabled={!avatarFile || busy === 'avatar'} onClick={saveAvatar}>{busy === 'avatar' ? '上传中…' : '保存头像 →'}</button>
        </div>
      </section>

      {error && <div className="profile-message error">{error}</div>}
      {notice && <div className="profile-message success">{notice}</div>}

      <div className="profile-grid">
        <section className="profile-panel">
          <div className="profile-panel-kicker">SECURITY</div>
          <h2>修改密码</h2>
          <p>只需输入新密码，不需要验证旧密码。修改后其他设备会退出登录。</p>
          <form onSubmit={savePassword} className="profile-form">
            <label className="auth-field"><span>新密码</span><input type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} placeholder="至少 6 位" /></label>
            <div className="profile-form-actions">
              <button className="btn btn-primary" type="submit" disabled={busy === 'password'}>{busy === 'password' ? '保存中…' : '保存新密码 →'}</button>
            </div>
          </form>
        </section>

        <section className="profile-panel profile-session">
          <div className="profile-panel-kicker">SESSION</div>
          <h2>当前账号</h2>
          <p>退出后需要重新登录才能投稿和打 tag。</p>
          <button className="btn btn-ghost" type="button" disabled={busy === 'logout'} onClick={logout}>{busy === 'logout' ? '退出中…' : '退出登录'}</button>
        </section>
      </div>
    </main>
  );
}
