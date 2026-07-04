/**
 * ActiveChatsScreen.jsx — שיחות של אנשי קשר ACTIVE
 *
 * API: vid.michal-solutions.com/api/active-chats/...
 * Props: phone — { id }
 *
 * src/screens/ActiveChatsScreen.jsx
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { apiFetch } from "../api";

function MsgBubble({ msg }) {
  const out = msg.direction === true;
  const t = msg.created_at
    ? new Date(msg.created_at).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" })
    : "";

  const body = () => {
    switch (msg.message_type) {
      case "image":
        return (<>{msg.metadata?.url && <img src={msg.metadata.url} alt="" style={{ maxWidth: "100%", borderRadius: 6, marginBottom: 4 }} />}{msg.content && <div>{msg.content}</div>}</>);
      case "audio":
        return msg.metadata?.url ? <audio controls src={msg.metadata.url} style={{ maxWidth: "100%" }} /> : <span>🎤</span>;
      case "file":
        return <a href={msg.metadata?.url} target="_blank" rel="noreferrer">📎 {msg.content || "קובץ"}</a>;
      case "buttons":
        return (<div><div>{msg.content}</div>{msg.metadata?.buttons && <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>{msg.metadata.buttons.map((b, i) => <span key={i} style={{ background: out ? "#b8e6d0" : "#e9edef", padding: "3px 8px", borderRadius: 6, fontSize: 12 }}>{b.displayText || b.text || b}</span>)}</div>}</div>);
      case "button_reply":
        return <span style={{ background: "#d5f0ff", padding: "2px 8px", borderRadius: 6, fontSize: 12 }}>↩ {msg.content}</span>;
      default:
        return <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{msg.content || "—"}</div>;
    }
  };

  return (
    <div style={{ display: "flex", justifyContent: out ? "flex-end" : "flex-start", marginBottom: 3, padding: "0 14px" }}>
      <div style={{
        background: out ? "#d9fdd3" : "#fff",
        borderRadius: out ? "8px 0 8px 8px" : "0 8px 8px 8px",
        padding: "6px 10px 3px", maxWidth: "70%",
        boxShadow: "0 1px .5px rgba(11,20,26,.13)", fontSize: 14, lineHeight: 1.35,
      }}>
        {msg.message_type !== "text" && <div style={{ fontSize: 10, color: "#667781", background: "#e9edef", display: "inline-block", padding: "0 5px", borderRadius: 4, marginBottom: 2 }}>{msg.message_type}</div>}
        {body()}
        <div style={{ textAlign: "left", fontSize: 11, color: "#667781", marginTop: 1 }}>
          {t}{out && <span style={{ marginRight: 4 }}>{msg.status === "read" ? "✓✓" : "✓"}</span>}
        </div>
      </div>
    </div>
  );
}

function DateSep({ date }) {
  return <div style={{ textAlign: "center", margin: "10px 0 6px" }}><span style={{ background: "#e1f2fb", color: "#54656f", fontSize: 12, padding: "3px 10px", borderRadius: 8 }}>{date}</span></div>;
}

function ContactItem({ c, on, onClick }) {
  const lm = c.last_message;
  const t = lm?.created_at ? new Date(lm.created_at).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" }) : "";
  return (
    <div onClick={onClick} style={{ padding: "10px 12px", cursor: "pointer", borderBottom: "1px solid #f0f2f5", background: on ? "#f0f6ff" : "transparent", display: "flex", alignItems: "center", gap: 10 }}>
      <div style={{ width: 42, height: 42, borderRadius: "50%", background: "#dfe5e7", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, color: "#fff", flexShrink: 0 }}>
        {(c.name || c.whatsapp_name || "?")[0]}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 500, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name || c.whatsapp_name || c.phone}</span>
          <span style={{ fontSize: 11, color: "#667781", flexShrink: 0 }}>{t}</span>
        </div>
        {lm && <div style={{ fontSize: 13, color: "#667781", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 2 }}>{lm.direction ? "✓ " : ""}{lm.content?.slice(0, 40) || `[${lm.message_type}]`}</div>}
        {!lm && c.call_count > 0 && <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>{c.call_count} שיחות</div>}
      </div>
    </div>
  );
}

export default function ActiveChatsScreen({ phone }) {
  const [contacts, setContacts] = useState([]);
  const [sel, setSel] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const chatEnd = useRef(null);
  const pid = phone?.id;

  useEffect(() => {
    if (!pid) return;
    setLoading(true);
    apiFetch(`/active-chats/${pid}/contacts`)
      .then(d => setContacts(d?.contacts || []))
      .catch(() => setContacts([]))
      .finally(() => setLoading(false));
  }, [pid]);

  const loadMessages = useCallback(() => {
    if (!sel || !pid) { setMessages([]); return; }
    setLoadingMsgs(true);
    apiFetch(`/active-chats/${pid}/contacts/${sel.id}/messages?limit=200`)
      .then(d => { setMessages(d?.messages || []); setTimeout(() => chatEnd.current?.scrollIntoView({ behavior: "smooth" }), 60); })
      .catch(() => setMessages([]))
      .finally(() => setLoadingMsgs(false));
  }, [sel, pid]);

  useEffect(() => { loadMessages(); }, [loadMessages]);
  useEffect(() => { if (!sel) return; const iv = setInterval(loadMessages, 5000); return () => clearInterval(iv); }, [sel, loadMessages]);

  const grouped = React.useMemo(() => {
    const out = []; let last = "";
    for (const m of messages) {
      const d = m.created_at ? new Date(m.created_at).toLocaleDateString("he-IL") : "";
      if (d !== last) { out.push({ type: "date", date: d }); last = d; }
      out.push({ type: "msg", msg: m });
    }
    return out;
  }, [messages]);

  return (
    <div style={{ display: "flex", height: "100%", fontFamily: "'Segoe UI',system-ui", direction: "rtl", color: "#111b21" }}>
      <div style={{ width: 280, borderLeft: "1px solid #e9edef", display: "flex", flexDirection: "column", background: "#fff", flexShrink: 0 }}>
        <div style={{ padding: "14px 16px", background: "#f0f2f5", borderBottom: "1px solid #e9edef", fontWeight: 700, fontSize: 16 }}>
          שיחות פעילות {contacts.length > 0 && <span style={{ fontSize: 13, fontWeight: 400, color: "#667781" }}>({contacts.length})</span>}
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {loading && <div style={{ padding: 24, textAlign: "center", color: "#8696a0" }}>טוען...</div>}
          {!loading && contacts.length === 0 && <div style={{ padding: 24, textAlign: "center", color: "#8696a0" }}>אין פעילים</div>}
          {contacts.map(c => <ContactItem key={c.id} c={c} on={sel?.id === c.id} onClick={() => setSel(c)} />)}
        </div>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "#efeae2" }}>
        <div style={{ padding: "10px 16px", background: "#f0f2f5", borderBottom: "1px solid #e9edef", display: "flex", alignItems: "center", gap: 10 }}>
          {sel ? (<><div style={{ width: 36, height: 36, borderRadius: "50%", background: "#dfe5e7", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16, color: "#fff" }}>{(sel.name||"?")[0]}</div><div><div style={{ fontWeight: 600, fontSize: 15 }}>{sel.name || sel.whatsapp_name || sel.phone}</div>{sel.phone && <div style={{ fontSize: 12, color: "#667781" }}>{sel.phone}</div>}</div></>) : <span style={{ color: "#667781" }}>בחר איש קשר</span>}
        </div>
        <div style={{ flex: 1, overflowY: "auto", paddingTop: 8, paddingBottom: 8 }}>
          {!sel && <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80 }}>בחר איש קשר</div>}
          {sel && loadingMsgs && messages.length === 0 && <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80 }}>טוען...</div>}
          {sel && !loadingMsgs && messages.length === 0 && <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80 }}>אין הודעות</div>}
          {grouped.map((item, i) => item.type === "date" ? <DateSep key={`d-${i}`} date={item.date} /> : <MsgBubble key={item.msg.id||i} msg={item.msg} />)}
          <div ref={chatEnd} />
        </div>
      </div>
    </div>
  );
}
