/**
 * ポータル経由の発注通知 → Slack 転送 GASスクリプト
 *
 * 目的: 発注データはポータル(Webサイト)にあり、メール本文には通知のみ来るシステム向け。
 *       通知メール到着を検知して Slack に「ポータルにログインしてDLしてください」と通知する。
 *
 * 対象システム:
 *   - スマクラ (smcl@cs-cxchange.net)
 *   - オリックス・ホテルマネジメント iporter (noreply_iporter@hotel.jp.nec.com)
 *   - その他は NOTIFY_PATTERNS に追記すれば自動的に対象になる
 *
 * 動作:
 *   1. NOTIFY_PATTERNS の各パターンに対して Gmail を検索
 *   2. 未通知の新着メールがあれば Slack へ転送
 *   3. 通知済みメールには「SCM/ポータル通知済み」ラベルを付与（重複防止）
 *
 * 設置方法:
 *   1. https://script.google.com/ を scmorderbox@two2.jp でログインして開く
 *   2. 「新しいプロジェクト」を作成（プロジェクト名: 「ポータル発注通知」）
 *   3. 下記コードを貼り付け
 *   4. SLACK_WEBHOOK_URL を設定（承認後）
 *   5. testSetup で動作確認
 *   6. トリガー設定: processPortalNotifications を 5分おきに実行
 */

// ============================================================
// 設定
// ============================================================

/** Slack Incoming Webhook URL（承認後に設定） */
const SLACK_WEBHOOK_URL = '';

/** 処理済みラベル */
const PROCESSED_LABEL = 'SCM/ポータル通知済み';

/** 今日以降の通知のみ対象 */
const SINCE_DATE = '2026/04/24';

/**
 * 監視対象パターン。新しいシステムを追加する時はここに追記するだけ。
 *
 * 各パターンのフィールド:
 *   name        : 表示用システム名
 *   sender      : Gmail検索の from: フィルタ
 *   subjectKey  : Gmail検索の subject: フィルタ（識別精度向上のため）
 *   portalUrl   : ポータルログインURL
 *   note        : Slack通知に追加する補足情報（手順や注意点）
 */
const NOTIFY_PATTERNS = [
  {
    name: 'スマクラ（スタイリングライフ・ホールディングス）',
    sender: 'smcl@cs-cxchange.net',
    subjectKey: '【スマクラ】',
    portalUrl: 'https://smcl.cs-cxchange.net/smcl2/Login.aspx',
    note: 'メール本文にログインIDが記載されています。CSV/Excelをダウンロードして `_自動取込/新着` フォルダへドロップしてください。',
  },
  {
    name: 'オリックス・ホテルマネジメント (iporter)',
    sender: 'noreply_iporter@hotel.jp.nec.com',
    subjectKey: '発注データ送信のお知らせ',
    portalUrl: 'https://iporter.nehops.com/ore/',
    note: '発注内容を確認・ダウンロードして `_自動取込/新着` フォルダへドロップしてください。',
  },
];


// ============================================================
// メイン処理
// ============================================================

function processPortalNotifications() {
  const label = getOrCreateLabel(PROCESSED_LABEL);
  let totalSent = 0;
  let totalErr = 0;

  for (const p of NOTIFY_PATTERNS) {
    try {
      const sent = processPattern(p, label);
      totalSent += sent;
    } catch (err) {
      totalErr++;
      console.error(`[${p.name}] 全体エラー: ${err}`);
      notifySlack(`:rotating_light: ポータル通知スクリプトエラー\nシステム: ${p.name}\n${err.message || err}`);
    }
  }

  console.log(`処理完了: 通知送信 ${totalSent}件 / エラー ${totalErr}件`);
}

function processPattern(pattern, label) {
  const query = `from:${pattern.sender} subject:"${pattern.subjectKey}" -label:${PROCESSED_LABEL} after:${SINCE_DATE}`;
  const threads = GmailApp.search(query, 0, 50);

  if (threads.length === 0) {
    console.log(`[${pattern.name}] 通知対象なし`);
    return 0;
  }

  let sent = 0;
  for (const thread of threads) {
    let threadHadError = false;
    for (const msg of thread.getMessages()) {
      // 念のため本物のメールか確認
      if (!msg.getFrom().includes(pattern.sender)) continue;

      try {
        sendSlackNotification(pattern, msg);
        sent++;
      } catch (err) {
        threadHadError = true;
        console.error(`[${pattern.name}] 通知送信失敗: ${msg.getSubject()} - ${err}`);
      }
    }
    if (!threadHadError) thread.addLabel(label);
  }
  console.log(`[${pattern.name}] ${sent}件 通知送信`);
  return sent;
}


// ============================================================
// Slack通知（リッチメッセージ）
// ============================================================

function sendSlackNotification(pattern, msg) {
  const subject = msg.getSubject() || '(件名なし)';
  const receivedAt = Utilities.formatDate(msg.getDate(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm');
  const gmailUrl = `https://mail.google.com/mail/u/0/#inbox/${msg.getId()}`;

  // メール本文の先頭抜粋（200文字）。ログインID等の情報があれば見える
  let bodyPreview = '';
  try {
    const plain = msg.getPlainBody() || '';
    bodyPreview = plain.replace(/\n+/g, '\n').substring(0, 300).trim();
  } catch (e) {
    bodyPreview = '(本文取得失敗)';
  }

  const text = `🔔 *${pattern.name}* に新着発注通知\n` +
    `件名: ${subject}\n` +
    `受信: ${receivedAt}\n` +
    `\n` +
    `ポータル: ${pattern.portalUrl}\n` +
    `${pattern.note}\n` +
    `\n` +
    `元メール: ${gmailUrl}\n` +
    `--- メール本文抜粋 ---\n${bodyPreview}`;

  notifySlack(text);
}

function notifySlack(text) {
  if (!SLACK_WEBHOOK_URL) {
    console.log('[Slack未設定] スキップ:\n' + text.substring(0, 200));
    return;
  }
  try {
    UrlFetchApp.fetch(SLACK_WEBHOOK_URL, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({ text: text }),
      muteHttpExceptions: true,
    });
  } catch (err) {
    console.error(`Slack通知失敗: ${err}`);
  }
}


// ============================================================
// ユーティリティ
// ============================================================

function getOrCreateLabel(name) {
  let label = GmailApp.getUserLabelByName(name);
  if (!label) label = GmailApp.createLabel(name);
  return label;
}


// ============================================================
// テスト用関数
// ============================================================

function testSetup() {
  console.log('=== ポータル通知 テスト開始 ===');

  for (const p of NOTIFY_PATTERNS) {
    try {
      const query = `from:${p.sender} subject:"${p.subjectKey}"`;
      const threads = GmailApp.search(query, 0, 5);
      console.log(`[${p.name}]`);
      console.log(`  ✅ Gmail検索OK: ${threads.length}件 (過去分含む)`);
      if (threads.length > 0) {
        const m = threads[0].getMessages()[0];
        console.log(`     最新: ${m.getSubject()}`);
        console.log(`     受信: ${Utilities.formatDate(m.getDate(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm')}`);
      }
    } catch (err) {
      console.error(`[${p.name}] ❌ 検索失敗: ${err}`);
    }
  }

  if (SLACK_WEBHOOK_URL) {
    notifySlack(':white_check_mark: ポータル通知スクリプト 設定テスト成功 (testSetup 実行)');
    console.log('✅ Slack通知テスト送信完了');
  } else {
    console.log('⚠️  Slack未設定（SLACK_WEBHOOK_URL が空）。通知ロジックは正常動作するが配信されません');
  }

  console.log('=== テスト完了 ===');
}

function runOnce() {
  processPortalNotifications();
}
