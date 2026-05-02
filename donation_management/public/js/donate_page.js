(function () {
  "use strict";

  const state = {
    fund: null,
    channel: null,
    amount: 0,
    frequency: "Once",
    isAnonymous: false,
    pageData: null,
  };

  const $ = (id) => document.getElementById(id);
  const fmt = (n) => "$" + Number(n || 0).toFixed(2);

  async function load() {
    const r = await fetch("/api/method/donation_management.api.public.get_donation_page_data");
    const data = (await r.json()).message;
    state.pageData = data;
    renderFunds(data.funds, data.default_fund);
    renderAmounts(data.suggested_amounts);
    renderChannels(data.channels);
    bindUI();
    refresh();
  }

  function renderFunds(funds, defaultFund) {
    const wrap = $("fund-list");
    wrap.innerHTML = "";
    funds.forEach((f) => {
      const card = document.createElement("div");
      card.className = "fund-card";
      card.dataset.fund = f.name;
      card.innerHTML = `
        <div style="font-weight:700;font-size:1.05rem">${f.icon || "•"} ${f.fund_name}</div>
        ${f.description ? `<div style="color:#555;font-size:.9rem;margin-top:.25rem">${f.description}</div>` : ""}
      `;
      card.addEventListener("click", () => {
        document.querySelectorAll(".fund-card").forEach((c) => c.classList.remove("active"));
        card.classList.add("active");
        state.fund = f.name;
        refresh();
      });
      wrap.appendChild(card);
      if (f.is_default || f.name === defaultFund) {
        card.click();
      }
    });
  }

  function renderAmounts(amounts) {
    const grid = $("amount-grid");
    grid.innerHTML = "";
    amounts.forEach((a) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = fmt(a);
      b.dataset.amount = a;
      b.addEventListener("click", () => {
        document.querySelectorAll("#amount-grid button").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        state.amount = a;
        $("custom-amount").value = "";
        refresh();
      });
      grid.appendChild(b);
    });
  }

  function renderChannels(channels) {
    const wrap = $("channel-list");
    wrap.innerHTML = "";
    if (!channels.length) {
      wrap.innerHTML = '<div style="color:#888">No payment channels are currently enabled. Please check back later.</div>';
      return;
    }
    channels.forEach((c) => {
      const card = document.createElement("div");
      card.className = "donate-channel-card";
      card.dataset.channel = c.name;
      card.dataset.recurring = c.supports_recurring ? "1" : "0";
      card.innerHTML = `
        <div class="icon">${c.icon || "💳"}</div>
        <div style="font-weight:700">${c.display_name || c.channel_name}</div>
        ${c.test_mode ? '<div style="color:#f0ad4e;font-size:.75rem">TEST MODE</div>' : ""}
      `;
      card.addEventListener("click", () => {
        if (state.frequency !== "Once" && card.dataset.recurring !== "1") {
          alert("This payment method doesn't support recurring giving. Pick another or change to one-time.");
          return;
        }
        document.querySelectorAll(".donate-channel-card").forEach((x) => x.classList.remove("active"));
        card.classList.add("active");
        state.channel = c.name;
        refresh();
      });
      wrap.appendChild(card);
    });
  }

  function bindUI() {
    document.querySelectorAll("#freq-toggle button").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#freq-toggle button").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        state.frequency = b.dataset.freq;
        // Filter channels that don't support recurring
        document.querySelectorAll(".donate-channel-card").forEach((c) => {
          if (state.frequency !== "Once" && c.dataset.recurring !== "1") {
            c.style.opacity = ".4";
            if (c.classList.contains("active")) {
              c.classList.remove("active");
              state.channel = null;
            }
          } else {
            c.style.opacity = "1";
          }
        });
        refresh();
      });
    });

    $("custom-amount").addEventListener("input", (e) => {
      state.amount = parseFloat(e.target.value) || 0;
      document.querySelectorAll("#amount-grid button").forEach((x) => x.classList.remove("active"));
      refresh();
    });

    $("anon-toggle").addEventListener("change", (e) => {
      state.isAnonymous = e.target.checked;
      $("donor-fields").style.opacity = e.target.checked ? ".5" : "1";
      refresh();
    });

    $("donate-cta").addEventListener("click", submit);
  }

  function refresh() {
    const ok =
      state.fund &&
      state.channel &&
      state.amount > 0 &&
      (state.isAnonymous || ($("donor-name").value.trim() && $("donor-email").value.trim()));
    $("donate-cta").disabled = !ok;

    if (state.fund && state.amount && state.channel) {
      $("donate-summary").style.display = "block";
      $("summary-total").textContent = fmt(state.amount);
      $("summary-freq").textContent = state.frequency === "Once" ? "today" : `every ${state.frequency.toLowerCase()}`;
      const fundObj = state.pageData.funds.find((f) => f.name === state.fund);
      const chObj = state.pageData.channels.find((c) => c.name === state.channel);
      $("summary-fund").textContent = fundObj ? fundObj.fund_name : state.fund;
      $("summary-channel").textContent = chObj ? chObj.display_name || chObj.channel_name : state.channel;
    } else {
      $("donate-summary").style.display = "none";
    }
    $("error-msg").textContent = "";
  }

  async function submit() {
    $("donate-cta").disabled = true;
    $("donate-cta").textContent = "Processing…";
    $("error-msg").textContent = "";

    const payload = {
      fund: state.fund,
      channel: state.channel,
      amount: state.amount,
      frequency: state.frequency,
      is_anonymous: state.isAnonymous ? 1 : 0,
      donor_name: $("donor-name").value.trim() || null,
      email: $("donor-email").value.trim() || null,
      phone: $("donor-phone").value.trim() || null,
      address_line_1: $("donor-addr1").value.trim() || null,
      address_line_2: $("donor-addr2").value.trim() || null,
      city: $("donor-city").value.trim() || null,
      state: $("donor-state").value.trim() || null,
      postal_code: $("donor-zip").value.trim() || null,
      country: $("donor-country").value.trim() || null,
      memo: $("donor-memo").value.trim() || null,
    };

    try {
      const r = await fetch("/api/method/donation_management.api.public.start_donation", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Frappe-CSRF-Token": (window.frappe && frappe.csrf_token) || "",
        },
        body: new URLSearchParams(payload),
      });
      const json = await r.json();
      if (!r.ok || json.exc) {
        throw new Error((json._server_messages && JSON.parse(json._server_messages)[0]) || "Submission failed.");
      }
      const result = json.message;
      handleResult(result);
    } catch (err) {
      $("error-msg").textContent = err.message || String(err);
      $("donate-cta").disabled = false;
      $("donate-cta").textContent = "Continue";
    }
  }

  function handleResult(result) {
    if (result.redirect_url) {
      window.location.href = result.redirect_url;
      return;
    }
    if (result.mode === "instructions" || result.mode === "pending_manual") {
      $("donate-cta").style.display = "none";
      const box = $("next-step");
      box.style.display = "block";
      box.innerHTML = `<h3 style="margin-top:0;color:#3a9e8a">${result.title || "Next steps"}</h3><div>${result.instructions || ""}</div><p style="margin-top:1rem;color:#666">Reference: <code>${result.donation}</code></p>`;
      box.scrollIntoView({ behavior: "smooth" });
      return;
    }
    window.location.href = "/donate/thanks?ref=" + encodeURIComponent(result.donation || "");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
