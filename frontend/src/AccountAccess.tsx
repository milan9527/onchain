import { useState } from "react";
import { KeyRound, X } from "lucide-react";
import { api } from "./api";

export function AccountAccess({ enabled, onEmail }: { enabled: boolean; onEmail: (email: string) => void }) {
  const [mode, setMode] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!enabled) return null;
  const open = (value: string) => { setMode(value); setPassword(""); setCode(""); setError(""); setMessage(""); };
  return <div className="account-access"><button type="button" className="text-btn" onClick={() => open("signup")}>创建账号</button><span>·</span><button type="button" className="text-btn" onClick={() => open("reset")}>忘记密码</button>{message && <small>{message}</small>}
    {mode && <div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-label="账号验证" className="modal"><div className="modal-heading"><h2>{mode === "signup" ? "创建账号" : mode === "confirm" ? "验证邮箱" : "重置密码"}</h2><button type="button" className="icon-btn" onClick={() => setMode("")} aria-label="关闭"><X /></button></div><form className="form-stack" onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError("");
      try {
        if (mode === "signup") {
          await api("/auth/signup", "POST", { email, password }); setMode("confirm"); setPassword("");
        } else if (mode === "confirm") {
          await api("/auth/confirm", "POST", { email, code }); setMode(""); onEmail(email); setMessage("邮箱已验证，请登录。");
        } else if (mode === "reset") {
          await api("/auth/password-reset", "POST", { email }); setMode("complete");
        } else {
          await api("/auth/password-reset", "POST", { email, code, password }); setMode(""); onEmail(email); setPassword(""); setMessage("密码已更新，请重新登录。");
        }
      } catch (e) { setError((e as Error).message); }
      finally { setBusy(false); }
    }}><label>账号邮箱<input type="email" value={email} onChange={e => setEmail(e.target.value)} required disabled={["confirm", "complete"].includes(mode)} /></label>{["confirm", "complete"].includes(mode) && <><p className="muted">输入发送到该邮箱的验证码。</p><label>邮箱验证码<input value={code} onChange={e => setCode(e.target.value)} autoComplete="one-time-code" required /></label></>}{["signup", "complete"].includes(mode) && <label>设置密码<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" minLength={12} required /><small className="muted">至少 12 位，包含大写、小写、数字和符号。</small></label>}{error && <p className="inline-error">{error}</p>}<button className="primary" disabled={busy}>{busy ? "处理中…" : "继续"}</button></form></section></div>}
  </div>;
}

export function MfaSettings({ email, enabled }: { email: string; enabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [enrollment, setEnrollment] = useState<any>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!enabled) return null;
  return <section className="panel mfa-panel"><KeyRound size={22} /><div><h3>验证器多因素认证</h3><p className="muted">{success ? "已启用。后续登录和操作重新认证需要验证器验证码。" : "启用后，登录与操作重新认证会同时验证密码和动态验证码。"}</p></div><button className="secondary" onClick={() => { setOpen(true); setError(""); setEnrollment(null); }}>设置验证器</button>{open && <div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-label="设置 MFA" className="modal"><div className="modal-heading"><h2>启用验证器</h2><button className="icon-btn" onClick={() => { setOpen(false); setPassword(""); setEnrollment(null); }} aria-label="关闭"><X /></button></div><form className="form-stack" onSubmit={async e => {
    e.preventDefault(); setBusy(true); setError("");
    try {
      if (!enrollment) {
        setEnrollment(await api("/auth/mfa/enroll", "POST", { email, password })); setPassword("");
      } else {
        await api("/auth/mfa/verify", "POST", { enrollment_id: enrollment.enrollment_id, code });
        setOpen(false); setSuccess(true); setEnrollment(null); setCode("");
      }
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }}>{!enrollment ? <label>重新输入密码<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label> : <><p className="muted">在验证器中手动添加账户，输入以下密钥，然后提交 6 位动态验证码。密钥仅用于当前设置，请勿发给 Agent。</p><code className="mfa-secret">{enrollment.secret}</code><label>动态验证码<input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} minLength={6} maxLength={6} required /></label></>}{error && <p className="inline-error">{error}</p>}<button className="primary" disabled={busy}>{enrollment ? "验证并启用" : "验证身份"}</button></form></section></div>}</section>;
}
