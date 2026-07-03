/**
 * ActiveChatsScreen.jsx
 *
 * שיחות scenario של אנשי קשר ACTIVE.
 * 3 פאנלים: אנשי קשר → calls → leaves (צ'אט)
 *
 * קורא מ-Spine דרך nginx proxy:
 *   GET /api/spine/api/phones/{phone_id}/active
 *   GET /api/spine/api/phones/{phone_id}/contacts/{contact_id}/calls
 *   GET /api/spine/api/calls/{call_id}/leaves
 *
 * אם proxy: location /api/spine/ { proxy_pass http://127.0.0.1:8100/; }
 * אז הנתיב בפועל הוא /api/spine/api/phones/...
 *
 * Props:
 *   phone — { id, phone_number }
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { apiFetch } from "../api";

const SPINE = "/spine/api";

const STATUS_ICON = {
  Pending: "⏳", Sent: "✓", Matched: "✅",
  Mismatched: "❌", Timeout: "⏰", Failed: "💥",
};

function Bubble({ leaf }) {
  const isBot = leaf.type === "Sender";
  const time = leaf.timestamp
    ? new Date(leaf.timestamp).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" })
    : "";
  return (
    <div style={{
      display: "flex", justifyContent: isBot ? "flex-end" : "flex-start",
      marginBottom: 3, padding: "0 14px",
    }}>
      <div style={{
        background: isBot ? "#d9fdd3" : "#fff",
        borderRadius: isBot ? "8px 0 8px 8px" : "0 8px 8px 8px",
        padding: "6px 10px 3px", maxWidth: "70%",
        boxShadow: "0 1px 0.5px rgba(11,20,26,.13)",
        fontSize: 14, lineHeight: 1.35,
      }}>
        {leaf.wa_type && leaf.wa_type !== "text" && (
          <div style={{ fontSize: 10, color: "#667781", background: "#e9edef", display: "inline-block", padding: "0 5px", borderRadius: 4, marginBottom: 2 }}>
            {leaf.wa_type}
          </div>
        )}
        <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{leaf.content || "—"}</div>
        <div style={{ textAlign: "left", fontSize: 11, color: "#667781", marginTop: 1 }}>
          {STATUS_ICON[leaf.status] || ""} {time}
        </div>
      </div>
    </div>
  );
}

function ContactItem({ contact, active, onClick }) {
  const lc = contact.last_call;
  const dot = lc?.status === "running" ? "🟢" : lc?.status === "completed" ? "✅" : lc?.status === "failed" ? "🔴" : "⚪";
  return (
    <div onClick={onClick} style={{
      padding: "10px 12px", cursor: "pointer", borderBottom: "1px solid #f0f2f5",
      background: active ? "#f0f6ff" : "transparent", display: "flex", alignItems: "center", gap: 10,
    }}>
      <div style={{
        width: 40, height: 40, borderRadius: "50%", background: "#dfe5e7",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 17, color: "#fff", flexShrink: 0,
      }}>
        {(contact.name || "?")[0]}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontWeight: 500, fontSize: 14 }}>{contact.name || contact.phone || contact.id?.slice(0, 10)}</span>
          <span style={{ fontSize: 11, color: "#667781" }}>{dot} {contact.call_count || 0}</span>
        </div>
        {lc && <div style={{ fontSize: 12, color: "#667781", marginTop: 1 }}>{lc.scenario_id?.slice(0, 16)} · {lc.status}</div>}
      </div>
    </div>
  );
}

function CallItem({ call, active, onClick }) {
  const color = { completed: "#027948", failed: "#e13f3f", running: "#0b80de", expired: "#c07a00" }[call.status] || "#667781";
  const t = call.started_at ? new Date(call.started_at).toLocaleString("he-IL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
  return (
    <div onClick={onClick} style={{
      padding: "8px 12px", cursor: "pointer", borderBottom: "1px solid #f0f2f5",
      background: active ? "#f0f6ff" : "transparent",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>{call.scenario_id || call.call_id?.slice(-10)}</span>
        <span style={{ fontSize: 10, fontWeight: 600, color, background: color + "14", padding: "1px 6px", borderRadius: 6 }}>{call.status}</span>
      </div>
      <div style={{ fontSize: 11, color: "#667781", marginTop: 2 }}>
        {t} · {call.duration_seconds || 0}s · ↗{call.sender_count || 0} ↙{call.expected_count || 0}
        {(call.mismatch_count || 0) > 0 && <span style={{ color: "#e13f3f" }}> ✕{call.mismatch_count}</span>}
      </div>
    </div>
  );
}

export default function ActiveChatsScreen({ phone }) {
  const [contacts, setContacts] = useState([]);
  const [selContact, setSelContact] = useState(null);
  const [calls, setCalls] = useState([]);
  const [selCall, setSelCall] = useState(null);
  const [leaves, setLeaves] = useState([]);
  const [loading, setLoading] = useState(false);
  const chatEnd = useRef(null);
  const phoneId = phone?.id;

  useEffect(() => {
    if (!phoneId) return;
    setLoading(true);
    apiFetch(`${SPINE}/phones/${phoneId}/active`)
      .then(d => setContacts(d?.contacts || []))
      .catch(() => setContacts([]))
      .finally(() => setLoading(false));
  }, [phoneId]);

  useEffect(() => {
    if (!selContact) { setCalls([]); return; }
    apiFetch(`${SPINE}/phones/${phoneId}/contacts/${selContact.id}/calls`)
      .then(d => setCalls(d?.calls || []))
      .catch(() => setCalls([]));
  }, [selContact, phoneId]);

  const loadLeaves = useCallback(() => {
    if (!selCall) { setLeaves([]); return; }
    apiFetch(`${SPINE}/calls/${selCall.call_id}/leaves`)
      .then(d => {
        setLeaves(d?.leaves || []);
        setTimeout(() => chatEnd.current?.scrollIntoView({ behavior: "smooth" }), 60);
      })
      .catch(() => {});
  }, [selCall]);

  useEffect(() => { loadLeaves(); }, [loadLeaves]);

  useEffect(() => {
    if (selCall?.status !== "running") return;
    const iv = setInterval(loadLeaves, 2000);
    return () => clearInterval(iv);
  }, [selCall, loadLeaves]);

  return (
    <div style={{ display: "flex", height: "100%", fontFamily: "'Segoe UI', system-ui, sans-serif", direction: "rtl", color: "#111b21" }}>

      {/* אנשי קשר */}
      <div style={{ width: 240, borderLeft: "1px solid #e9edef", display: "flex", flexDirection: "column", background: "#fff", flexShrink: 0 }}>
        <div style={{ padding: "12px 14px", background: "#f0f2f5", borderBottom: "1px solid #e9edef", fontWeight: 700, fontSize: 15 }}>
          אנשי קשר פעילים {contacts.length > 0 && <span style={{ fontSize: 12, fontWeight: 400, color: "#667781" }}>({contacts.length})</span>}
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {loading && <div style={{ padding: 20, textAlign: "center", color: "#8696a0" }}>טוען...</div>}
          {!loading && contacts.length === 0 && <div style={{ padding: 20, textAlign: "center", color: "#8696a0" }}>אין פעילים</div>}
          {contacts.map(c => <ContactItem key={c.id} contact={c} active={selContact?.id === c.id} onClick={() => { setSelContact(c); setSelCall(null); }} />)}
        </div>
      </div>

      {/* שיחות */}
      <div style={{ width: 240, borderLeft: "1px solid #e9edef", display: "flex", flexDirection: "column", background: "#fff", flexShrink: 0 }}>
        <div style={{ padding: "12px 14px", background: "#f0f2f5", borderBottom: "1px solid #e9edef", fontWeight: 600, fontSize: 14 }}>
          שיחות {selContact ? `· ${selContact.name || ""}` : ""}
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {!selContact && <div style={{ padding: 20, textAlign: "center", color: "#8696a0" }}>בחר איש קשר</div>}
          {selContact && calls.length === 0 && <div style={{ padding: 20, textAlign: "center", color: "#8696a0" }}>אין שיחות</div>}
          {calls.map(c => <CallItem key={c.call_id} call={c} active={selCall?.call_id === c.call_id} onClick={() => setSelCall(c)} />)}
        </div>
      </div>

      {/* צ'אט */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "#efeae2" }}>
        <div style={{ padding: "10px 16px", background: "#f0f2f5", borderBottom: "1px solid #e9edef", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>{selCall ? selCall.call_id?.slice(-16) : "בחר שיחה"}</span>
          {selCall?.status === "running" && <span style={{ fontSize: 11, fontWeight: 600, color: "#027948", background: "#dcf8e6", padding: "2px 8px", borderRadius: 8 }}>● LIVE</span>}
        </div>
        <div style={{ flex: 1, overflowY: "auto", paddingTop: 8, paddingBottom: 8 }}>
          {!selCall && <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80 }}>בחר שיחה</div>}
          {selCall && leaves.length === 0 && <div style={{ textAlign: "center", color: "#8696a0", marginTop: 80 }}>אין הודעות</div>}
          {leaves.map((leaf, i) => <Bubble key={leaf.leaf_id || i} leaf={leaf} />)}
          <div ref={chatEnd} />
        </div>
      </div>
    </div>
  );
}
