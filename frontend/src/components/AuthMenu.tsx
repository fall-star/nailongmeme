import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { AuthMode, AuthUser } from '../lib/auth-client';
import { getCurrentUser, openAuth } from '../lib/auth-client';
import AuthDialog from './AuthDialog';

export default function AuthMenu() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<AuthMode>('login');
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser().then((value) => {
      if (!cancelled) {
        setUser(value);
        setLoading(false);
      }
    });
    function onOpen(event: Event) {
      const detail = (event as CustomEvent<{ mode?: AuthMode }>).detail;
      setMode(detail?.mode ?? 'login');
      setOpen(true);
    }
    function onAuthChanged(event: Event) {
      const detail = (event as CustomEvent<{ user?: AuthUser | null }>).detail;
      if (detail && 'user' in detail) {
        setUser(detail.user ?? null);
        setLoading(false);
      }
    }
    window.addEventListener('open-auth', onOpen);
    window.addEventListener('auth-changed', onAuthChanged);
    return () => {
      cancelled = true;
      window.removeEventListener('open-auth', onOpen);
      window.removeEventListener('auth-changed', onAuthChanged);
    };
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    function onDocClick(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [menuOpen]);

  function acceptUser(next: AuthUser) {
    setUser(next);
    setLoading(false);
    window.dispatchEvent(new CustomEvent('auth-changed', { detail: { user: next } }));
  }

  async function logout() {
    setMenuOpen(false);
    await fetch('/api/auth/logout', { method: 'POST' });
    setUser(null);
    window.dispatchEvent(new CustomEvent('auth-changed', { detail: { user: null } }));
  }

  return (
    <>
    <div className="auth-menu" ref={menuRef}>
      {loading ? (
        <span className="auth-loading" aria-hidden="true" />
      ) : user ? (
        <div className="auth-user-wrap">
          <button className="auth-user" type="button" onClick={() => setMenuOpen((value) => !value)} aria-expanded={menuOpen} aria-label="账户菜单">
            <img src={user.avatar_url} alt="" />
            <span>{user.nickname}</span>
            <b>⌄</b>
          </button>
          {menuOpen && (
            <div className="auth-dropdown">
              <div className="auth-dropdown-head"><strong>{user.nickname}</strong><small>{user.is_admin ? '管理员' : '已登录'}</small></div>
              <a className="auth-dropdown-link" href="/account" onClick={() => setMenuOpen(false)}>个人信息</a>
              <button type="button" onClick={logout}>退出登录</button>
            </div>
          )}
        </div>
      ) : (
        <div className="auth-guest">
          <button className="auth-login" type="button" onClick={() => openAuth('login')}>登录</button>
          <button className="auth-register" type="button" onClick={() => openAuth('register')}>注册</button>
        </div>
      )}
    </div>
    {typeof document !== 'undefined' && createPortal(
      <AuthDialog open={open} mode={mode} onClose={() => setOpen(false)} onAuth={acceptUser} />,
      document.body,
    )}
    </>
  );
}
