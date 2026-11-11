export type AuthUser = {
  id: string;
  nickname: string;
  email: string;
  avatar_url: string;
  is_admin: boolean;
};

export type AuthMode = 'login' | 'register';

export function openAuth(mode: AuthMode = 'login') {
  window.dispatchEvent(new CustomEvent('open-auth', { detail: { mode } }));
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  try {
    const response = await fetch('/api/auth/me', { cache: 'no-store' });
    if (!response.ok) return null;
    const data = await response.json();
    return data.user ?? null;
  } catch {
    return null;
  }
}
