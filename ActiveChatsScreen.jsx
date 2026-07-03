/**
 * ActiveChatsScreen.jsx
 *
 * מסך שיחות של אנשי קשר ACTIVE.
 * שני פאנלים: רשימת contacts → הודעות WhatsApp
 *
 * API קיים (לא צריך FastAPI חדש):
 *   GET /phones/{phone_id}/contacts/active → { contacts: [...] }
 *   GET /messages/phone/{phone_id}/contact/{contact_id}?limit=200 → { messages: [...] }
 *
 * שימוש:
 *   ב-PhoneDetail, כטאב:
 *   <ActiveChatsScreen phone={phone} />
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { apiFetch } from "../api";

// ── בועת הודעה ──────────────────────────────────────────────────────────────

function MessageBubble({ msg }) {
  const isOut = msg.direction === true; // true = בוט/יוצא, false = נכנס
  const time = msg.created_at
    ? new Date(msg.created_at).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" })
    : "";

  const renderContent = () => {
    switch (msg.message_type) {
      case "image":
        return (
          <>
            {msg.media_url && (
              <img src={msg.media_url} alt="" style={{ maxWidth: "100%", borderRadius: 6, marginBottom: 4 }} />
            )}
            {msg.content && <div>{msg.content}</div>}
          </>
        );
      case "audio":
        return msg.media_url
          ? <audio controls src={msg.media_url} style={{ maxWidth: "100%" }} />
          : <span>🎤 הודעה קולית</span>;
      case "file":
        return (
          <a href={msg.media_url} target="_blank" rel="noreferrer" style={{ color: "#027eb5" }}>
            📎 {msg.content || "קובץ"}
          </a>
        );
      case "buttons":
      case "menu":
        return (
          <div>
            <div>{msg.content}</div>
            {msg.metadata?.buttons && (
              <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                {msg.metadata.buttons.map((b, i) => (
                  <span key={i} style={{
                    background: isOut ? "#b8e6d0" : "#e9edef",
                    padding: "3px 8px", borderRadius: 6, fontSize: 12,
                  }}>
                    {b.displayText || b.text || b}
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      case "button_reply":
        return (
          <span style={{
            background: "#d5f0ff", padding: "2px 8px",
            borderRadius: 6, fontSize: 12,
          }}>
            ↩ {msg.content}
          </span>
        );
      default:
        return <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{msg.content || "—"}</div>;
    }
  };

  return (
    <div style={{
      display: "flex",
      justifyContent: isOut ? "flex-end" : "flex-start",
      marginBottom: 3, padding: "0 14px",
    }}>
      <div style={{
        background: isOut ? "#d9fdd3" : "#fff",
        borderRadius: isOut ? "8px 0 8px 8px" : "0 8px 8px 8px",
        padding: "6px 10px 3px", maxWidth: "70%",
        boxShadow: "0 1px 0.5px rgba(11,20,26,.13)",
        fontSize: 14, lineHeight: 1.35,
      }}>
        {renderContent()}
        <div style={{ textAlign: "left", fontSize: 11, color: "#667781", marginTop: 1 }}>
          {time}
          {isOut && <span style={{ marginRight: 4 }}>{msg.status === "read" ? "✓✓" : "✓"}</span>}
        </div>
      </div>
    </div>
  );
}

// ── מפריד תאריך ─────────────────────────────────────────────────────────────

function DateSep({ date }) {
  return (
    <div style={{ textAlign: "center", margin: "12px 0 8px" }}>
      <span style={{
        background: "#e1f2fb", color: "#54656f",
        fontSize: 12, padding: "4px 12px", borderRadius: 8,
        boxShadow: "0 1px 0.5px rgba(11,20,26,.13)",
      }}>
        {date}
      </span>
    </div>
  );
}

// ── איש קשר ──────────────────────────────────────────────────────────────────

function ContactItem({ contact, active, onClick }) {
  return (
    <div onClick={onClick} style={{
      padding: "10px 12px", cursor: "pointer",
      borderBottom: "1px solid #f0f2f5",
      background: active ? "#f0f6ff" : "transparent",
      display: "flex", alignItems: "center", gap: 10,
    }}>
      <div style={{
        width: 42, height: 42, borderRadius: "50%", background: "#dfe5e7",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 18, color: "#fff", flexShrink: 0,
      }}>
        {(contact.name || "?")[0]}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontWeight: 500, fontSize: 15, color: "#111b21",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {contact.name || contact.phone || contact.id?.slice(0, 10)}
        </div>
        {contact.phone && (
          <div style={{ fontSize: 12, color: "#667781", marginTop: 1 }}>{contact.phone}</div>
        )}
      </div>
    </div>
  );
}

// ── מסך ראשי ─────────────────────────────────────────────────────────────────

export default function ActiveChatsScreen({ phone }) {
  const [contacts, setContacts] = useState([]);
  const [selContact, setSelContact] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const chatEnd = useRef(null);

  const phoneId = phone?.id;

  // טען אנשי קשר active
  useEffect(() => {
    if (!phoneId) return;
    setLoading(true);
    apiFetch(`/phones/${phoneId}/contacts/active`)
      .then(d => setContacts(d?.contacts || []))
      .catch(() => setContacts([]))
      .finally(() => setLoading(false));
  }, [phoneId]);

  // טען הודעות לאיש קשר שנבחר
  const loadMessages = useCallback(() => {
    if (!selContact || !phoneId) { setMessages([]); return; }
    setLoadingMsgs(true);
    apiFetch(`/messages/phone/${phoneId}/contact/${selContact.id}?limit=200`)
      .then(d => {
        setMessages(d?.messages || d || []);
        setTimeout(() => chatEnd.current?.scrollIntoView({ behavior: "smooth" }), 80);
      })
      .catch(() => setMessages([]))
      .finally(() => setLoadingMsgs(false));
  }, [selContact, phoneId]);

  useEffect(() => { loadMessages(); }, [loadMessages]);

  // auto-refresh כל 5 שניות
  useEffect(() => {
    if (!selContact) return;
    const iv = setInterval(loadMessages, 5000);
    return () => clearInterval(iv);
  }, [selContact, loadMessages]);

  // קבץ לפי תאריך
  const grouped = React.useMemo(() => {
    const out = [];
    let lastDate = "";
    for (const msg of messages) {
      const d = msg.created_at ? new Date(msg.created_at).toLocaleDateString("he-IL") : "";
      if (d !== lastDate) { out.push({ type: "date", date: d }); lastDate = d; }
      out.push({ type: "msg", msg });
    }
    return out;
  }, [messages]);

  return (
    <div style={{
      display: "flex", height: "100%",
      fontFamily: "'Segoe UI', system-ui, sans-serif",
      direction: "rtl", color: "#111b21",
    }}>

      {/* רשימת אנשי קשר */}
      <div style={{
        width: 280, borderLeft: "1px solid #e9edef",
        display: "flex", flexDirection: "column",
        background: "#fff", flexShrink: 0,
      }}>
        <div style={{
          padding: "14px 16px", background: "#f0f2f5",
          borderBottom: "1px solid #e9edef", fontWeight: 700, fontSize: 16,
        }}>
          שיחות פעילות
          {contacts.length > 0 && (
            <span style={{ fontSize: 13, fontWeight: 400, color: "#667781", marginRight: 6 }}>
              ({contacts.length})
            </span>
          )}
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {loading && <div style={{ padding: 24, textAlign: "center", color: "#8696a0" }}>טוען...</div>}
          {!loading && contacts.length === 0 && (
            <div style={{ padding: 24, textAlign: "center", color: "#8696a0" }}>אין אנשי קשר פעילים</div>
          )}
          {contacts.map(c => (
            <ContactItem key={c.id} contact={c}
              active={selContact?.id === c.id}
              onClick={() => setSelContact(c)} />
          ))}
        </div>
      </div>

      {/* הודעות */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "#efeae2" }}>
        {/* Header */}
        <div style={{
          padding: "10px 16px", background: "#f0f2f5",
          borderBottom: "1px solid #e9edef",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          {selContact ? (
            <>
              <div style={{
                width: 36, height: 36, borderRadius: "50%", background: "#dfe5e7",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 16, color: "#fff",
              }}>
                {(selContact.name || "?")[0]}
              </div>
              <div>
                <div style={{ fontWeight: 600, fontSize: 15 }}>{selContact.name || selContact.phone}</div>
                {selContact.phone && <div style={{ fontSize: 12, color: "#667781" }}>{selContact.phone}</div>}
              </div>
            </>
          ) : (
            <span style={{ color: "#667781", fontSize: 14 }}>בחר איש קשר מהרשימה</span>
          )}
        </div>

        {/* Messages */}
        <div style={{ flex: 1, overflowY: "auto", paddingTop: 8, paddingBottom: 8 }}>
          {!selContact && (
            <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80, fontSize: 14 }}>
              בחר איש קשר כדי לצפות בשיחה
            </div>
          )}
          {selContact && loadingMsgs && messages.length === 0 && (
            <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80 }}>טוען הודעות...</div>
          )}
          {selContact && !loadingMsgs && messages.length === 0 && (
            <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80 }}>אין הודעות</div>
          )}
          {grouped.map((item, i) =>
            item.type === "date"
              ? <DateSep key={`d-${i}`} date={item.date} />
              : <MessageBubble key={item.msg.id || i} msg={item.msg} />
          )}
          <div ref={chatEnd} />
        </div>
      </div>
    </div>
  );
}
