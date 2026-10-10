// Vercel Serverless Function: Hermes AI Lead Intake & Telegram Dispatcher

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "";
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID || "";
const GROQ_API_KEY = process.env.GROQ_API_KEY || "";
const DISCORD_BOT_TOKEN = process.env.DISCORD_BOT_TOKEN || "";
const DISCORD_CHANNEL_ID = process.env.DISCORD_CHANNEL_ID || "";

async function sendTelegram(text, reply_markup = null) {
  if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) return false;
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
  const body = {
    chat_id: TELEGRAM_CHAT_ID,
    text: text,
    parse_mode: "Markdown",
  };
  if (reply_markup) body.reply_markup = reply_markup;

  try {
    let res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      // Fallback without parse_mode in case Markdown has special characters
      delete body.parse_mode;
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    return res.ok;
  } catch (err) {
    console.error("Telegram error:", err);
    return false;
  }
}

async function sendDiscord(title, description, fields = []) {
  if (!DISCORD_BOT_TOKEN || !DISCORD_CHANNEL_ID) return false;
  const url = `https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages`;
  try {
    await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        embeds: [
          {
            title,
            description,
            color: 0x0ea5e9,
            fields,
            timestamp: new Date().toISOString(),
          },
        ],
      }),
    });
    return true;
  } catch (e) {
    console.error("Discord error:", e);
    return false;
  }
}

async function queryGroqAI(messages, temperature = 0.5) {
  if (!GROQ_API_KEY) return null;
  const models = ["qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "llama3-70b-8192"];
  for (const model of models) {
    try {
      const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${GROQ_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model,
          messages,
          temperature,
          max_tokens: 650,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        return data.choices?.[0]?.message?.content || null;
      }
    } catch (e) {
      console.warn(`Groq error on ${model}:`, e.message);
    }
  }
  return null;
}

export default async function handler(req, res) {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS, GET");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method === "GET") {
    return res.status(200).json({ status: "online", service: "Hermes Lead Notification Gateway" });
  }

  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  try {
    const body = typeof req.body === "string" ? JSON.parse(req.body) : req.body || {};
    const { type = "lead", name, phone, email, requirement, budget, timeline, messages } = body;

    // 1. INSTANT DIRECT CALL CLICK NOTIFICATION
    if (type === "instant_call") {
      const clientInfo = name || phone ? `${name || "Unknown"} (${phone || "No phone"})` : "Website Visitor";
      const tgText = `🚨 *INSTANT CALL CLICKED* 🚨\n━━━━━━━━━━━━━━━━━━━━\n👤 *Client:* ${clientInfo}\n⚡ *Action:* Visitor just clicked *Call Mohd Ashfaq* on the portfolio!\n📱 *Phone:* [Call Back](tel:${phone || "+916303862018"})\n⏱ *Time:* ${new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })}`;
      
      await sendTelegram(tgText);
      await sendDiscord("🚨 Inbound Call Intent", `Visitor clicked Direct Call: ${clientInfo}`);
      return res.status(200).json({ success: true, message: "Hermes notified" });
    }

    // 2. INTERACTIVE CHAT WITH HERMES
    if (type === "chat") {
      const chatHistory = Array.isArray(messages) ? messages : [];
      const systemPrompt = {
        role: "system",
        content: `You are Hermes, the Autonomous AI Architect & Chief Systems Agent for Mohd Ashfaq (AI Automation & Integration Engineer based in Hyderabad, India).
Ashfaq specializes in:
1. End-to-End Workflow Automation (n8n, Python, Zapier, Webhooks, CRM & WhatsApp sync).
2. AI Connected to Tools & Databases (MCP servers, Claude & GPT API, Hybrid RAG, local Ollama models).
3. Computer Vision & Edge AI (YOLOv8, Raspberry Pi, automated monitoring & alert feeds).
4. Custom Autonomous AI agents & Turnkey software deliverables.

Your mission:
- Greet the client politely, professionally, and sharply.
- Ask clarifying technical or operational questions about their bottleneck.
- Provide a brief, confidence-inspiring architecture idea (2-3 concise sentences).
- Encourage them to book a free 20-minute call or share their WhatsApp/Phone number so Ashfaq can build it.
- Keep responses compact, punchy, and helpful. Do not write long essays.`
      };

      const aiReply = await queryGroqAI([systemPrompt, ...chatHistory], 0.7) || 
        "I'd love to help automate that! Ashfaq builds custom end-to-end workflows with n8n, Claude/GPT APIs, and WhatsApp integrations. Leave your WhatsApp number or click 'Book Free Call' above, and we can discuss the exact architecture for your project.";

      // Check if client shared contact info in chat
      const lastUserMsg = chatHistory[chatHistory.length - 1]?.content || "";
      const hasContact = /(\+?\d{10,13}|[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)/i.test(lastUserMsg);
      if (hasContact) {
        const notifyText = `💬 *HERMES LIVE CHAT CONTACT CAPTURED*\n━━━━━━━━━━━━━━━━━━━━\n📝 *Client Message:* ${lastUserMsg}\n🤖 *Hermes Reply:* ${aiReply.substring(0, 160)}...\n⏱ *Time:* ${new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })}`;
        sendTelegram(notifyText);
      }

      return res.status(200).json({ success: true, reply: aiReply });
    }

    // 3. FULL LEAD / PROJECT INTAKE DISPATCH
    const clientName = name || "Anonymous Client";
    const clientPhone = phone || "Not provided";
    const clientEmail = email || "Not provided";
    const clientReq = requirement || "General Automation Inquiry";
    const clientBudget = budget || "To be discussed";
    const clientTimeline = timeline || "Immediate";

    // Run Hermes AI Scoring & Scoping
    let aiEvaluation = {
      score: 85,
      temperature: "HOT",
      summary: "Inbound workflow automation inquiry requiring custom integration.",
      suggested_stack: "FastAPI, n8n, Claude/Groq API, WhatsApp Webhook",
      recommended_action: "Schedule 20-min discovery call immediately."
    };

    try {
      const aiPrompt = `Analyze this client project inquiry for AI Automation Engineer Mohd Ashfaq.
Client: ${clientName}
Phone: ${clientPhone}
Email: ${clientEmail}
Project Requirement: ${clientReq}
Budget: ${clientBudget}
Timeline: ${clientTimeline}

Respond with pure JSON only in this format:
{
  "score": <0-100 integer>,
  "temperature": "HOT"|"WARM"|"COLD",
  "summary": "<1-2 sentence executive assessment>",
  "suggested_stack": "<comma separated key technologies>",
  "recommended_action": "<actionable advice for Ashfaq on the call>"
}`;
      const rawAi = await queryGroqAI([
        { role: "system", content: "You are Hermes AI, autonomous lead qualifier. Output valid JSON only." },
        { role: "user", content: aiPrompt }
      ], 0.2);

      if (rawAi) {
        const jsonMatch = rawAi.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          aiEvaluation = JSON.parse(jsonMatch[0]);
        }
      }
    } catch (e) {
      console.warn("AI scoring parse fallback:", e);
    }

    // Clean phone for WhatsApp link
    const cleanPhone = clientPhone.replace(/[^0-9]/g, "");
    const waClientLink = cleanPhone ? `https://wa.me/${cleanPhone}` : null;
    const cleanAshfaqPhone = "916303862018";
    const waAshfaqLink = `https://wa.me/${cleanAshfaqPhone}?text=${encodeURIComponent(`Hi Ashfaq, I am ${clientName}. I just submitted my project inquiry on your portfolio regarding: ${clientReq.substring(0, 100)}`)}`;

    // Build Telegram Notification
    const scoreEmoji = (aiEvaluation.score || 80) >= 75 ? "🔥" : "⚡";
    const tgMessage = 
`${scoreEmoji} *NEW CLIENT INQUIRY & CALL REQUEST*
━━━━━━━━━━━━━━━━━━━━
👤 *Client:* ${clientName}
📱 *Phone / WhatsApp:* ${clientPhone}
📧 *Email:* ${clientEmail}
💰 *Budget:* ${clientBudget}
⏱ *Timeline:* ${clientTimeline}

📝 *Project Requirement:*
${clientReq}

━━━━━━━━━━━━━━━━━━━━
🤖 *Hermes AI Scoping:*
• *Deal Score:* ${aiEvaluation.score || 85}/100 (*${aiEvaluation.temperature || "HOT"}*)
• *Summary:* ${aiEvaluation.summary || "High priority client request."}
• *Stack:* \`${aiEvaluation.suggested_stack || "Python, LLM, Webhooks"}\`
• *Strategy:* ${aiEvaluation.recommended_action || "Reach out directly via WhatsApp/Phone."}
`;

    // Inline Keyboard Buttons for 1-tap action
    const inlineKeyboard = [];
    const contactRow = [];
    if (waClientLink) {
      contactRow.push({ text: "💬 WhatsApp Client", url: waClientLink });
    }
    if (clientPhone && clientPhone !== "Not provided") {
      contactRow.push({ text: "📞 Call Client", url: `tel:${clientPhone}` });
    }
    if (contactRow.length > 0) inlineKeyboard.push(contactRow);

    inlineKeyboard.push([
      { text: "⚡ Hermes Autonomous Action", callback_data: `hermes_lead_${Date.now()}` }
    ]);

    // Send Alerts in Parallel
    await Promise.allSettled([
      sendTelegram(tgMessage, { inline_keyboard: inlineKeyboard }),
      sendDiscord(
        `🚨 Inbound Lead: ${clientName} (${aiEvaluation.score}/100 ${aiEvaluation.temperature})`,
        clientReq,
        [
          { name: "Phone", value: clientPhone, inline: true },
          { name: "Email", value: clientEmail, inline: true },
          { name: "Budget", value: clientBudget, inline: true },
          { name: "Timeline", value: clientTimeline, inline: true },
          { name: "Recommended Stack", value: aiEvaluation.suggested_stack || "Custom", inline: false }
        ]
      )
    ]);

    return res.status(200).json({
      success: true,
      message: "Lead received and Hermes notified Ashfaq successfully",
      ai_evaluation: aiEvaluation,
      whatsapp_url: waAshfaqLink
    });

  } catch (error) {
    console.error("Handler error:", error);
    return res.status(500).json({ error: "Internal server error", details: error.message });
  }
}
