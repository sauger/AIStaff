import { useState } from 'react';

import artwork from '../assets/staffdeck/login-employees.svg?raw';
import { api, TENANT_ID } from '../api/client';
import { setEnterpriseAuthSession, type EnterpriseAuthSession } from '../auth';

import './LoginPage.css';

export type LoginPageProps = {
  onLogin: (session: EnterpriseAuthSession) => void;
};

const REMEMBERED_USERNAME_KEY = 'staffdeck_login_username';

function rememberedUsername(): string {
  try {
    return window.localStorage.getItem(REMEMBERED_USERNAME_KEY) || '';
  } catch {
    return '';
  }
}

function LoginField({
  password = false,
  value,
  invalid,
  onChange,
}: {
  password?: boolean;
  value: string;
  invalid: boolean;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="login-field" data-invalid={invalid ? 'true' : undefined}>
      <svg
        width="18"
        height="18"
        viewBox="0 0 18 18"
        fill="none"
        stroke="#8C9092"
        strokeWidth="1.2"
        aria-hidden="true"
      >
        {password ? (
          <>
            <rect x="2" y="9" width="14" height="7" rx="2" />
            <path d="M5 9V5a4 4 0 0 1 8 0v4M7 12.5h4" />
          </>
        ) : (
          <>
            <rect x="2" y="2" width="14" height="14" rx="4.5" />
            <path d="M7 12.5h4" />
          </>
        )}
      </svg>
      <input
        required
        aria-label={password ? '密码' : '账号'}
        placeholder={password ? '请输入密码' : '请输入账号'}
        type={password && !visible ? 'password' : 'text'}
        autoComplete={password ? 'current-password' : 'username'}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {password && (
        <button
          type="button"
          className="login-eye"
          aria-label={visible ? '隐藏密码' : '显示密码'}
          aria-pressed={visible}
          onClick={() => setVisible(!visible)}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 18 18"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            aria-hidden="true"
          >
            {visible ? (
              <>
                <path d="M1 9s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z" />
                <circle cx="9" cy="9" r="2" />
              </>
            ) : (
              <>
                <path d="M2 6s3 4 7 4 7-4 7-4M4 9l-2 3m7-2v3m5-4 2 3" />
              </>
            )}
          </svg>
        </button>
      )}
    </label>
  );
}

/**
 * Design login page (`LoginPage.jsx` in the RapidStaff prototype): one centered
 * card with the animated employee artwork, account/password fields, remember
 * option and the primary login action. Login is wired to the StaffDeck API.
 */
export default function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState(rememberedUsername);
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(() => Boolean(rememberedUsername()));
  const [remembered] = useState(rememberedUsername);
  const [notice, setNotice] = useState('');
  const [invalid, setInvalid] = useState<'username' | 'password' | 'both' | null>(null);
  const [loading, setLoading] = useState(false);

  async function login() {
    const trimmedUsername = username.trim();
    const trimmedPassword = password.trim();
    if (!trimmedUsername) {
      setInvalid('username');
      setNotice('请输入账号');
      return;
    }
    if (!trimmedPassword) {
      setInvalid('password');
      setNotice('请输入密码');
      return;
    }

    setInvalid(null);
    setNotice('');
    setLoading(true);
    try {
      const session = await api.post<EnterpriseAuthSession>('/api/auth/login', {
        tenant_id: TENANT_ID,
        username: trimmedUsername,
        password: trimmedPassword,
      });
      setEnterpriseAuthSession(session);
      try {
        if (remember) window.localStorage.setItem(REMEMBERED_USERNAME_KEY, trimmedUsername);
        else window.localStorage.removeItem(REMEMBERED_USERNAME_KEY);
      } catch {
        // Login still succeeds when storage is unavailable.
      }
      onLogin(session);
    } catch (error) {
      setInvalid('both');
      setNotice(error instanceof Error && error.message ? error.message : '登录失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-label="登录 RapidStaff">
        <div
          className="login-art"
          role="img"
          aria-label="RapidStaff 数字员工"
          dangerouslySetInnerHTML={{ __html: artwork }}
        />
        <form
          className="login-form"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void login();
          }}
        >
          <LoginField
            value={username}
            invalid={invalid === 'username' || invalid === 'both'}
            onChange={(value) => {
              setUsername(value);
              setInvalid(null);
              setNotice('');
            }}
          />
          <LoginField
            password
            value={password}
            invalid={invalid === 'password' || invalid === 'both'}
            onChange={(value) => {
              setPassword(value);
              setInvalid(null);
              setNotice('');
            }}
          />
          <div className="login-options">
            <label className="login-remember">
              <input
                type="checkbox"
                checked={remember}
                onChange={(event) => {
                  const next = event.target.checked;
                  setRemember(next);
                  if (!next) {
                    try {
                      window.localStorage.removeItem(REMEMBERED_USERNAME_KEY);
                    } catch {
                      // Ignore storage failures.
                    }
                  } else if (remembered) {
                    try {
                      window.localStorage.setItem(REMEMBERED_USERNAME_KEY, remembered);
                    } catch {
                      // Ignore storage failures.
                    }
                  }
                }}
              />
              记住密码
            </label>
            <button
              type="button"
              className="login-forgot"
              onClick={() => setNotice('密码找回尚未接入，请联系管理员。')}
            >
              忘记密码?
            </button>
          </div>
          <button className="login-submit" type="submit" disabled={loading}>
            {loading ? '登录中…' : '立即登录'}
          </button>
        </form>
        {notice && (
          <p className="login-notice" role="status" data-tone={invalid ? 'error' : undefined}>
            {notice}
          </p>
        )}
      </section>
    </main>
  );
}
