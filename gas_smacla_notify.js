/**
 * スマクラ受注通知 Slack連携 GAS
 *
 * 設置: 2foods-scm@two2.jp の Apps Script
 * 5分おきトリガー: processSmaclaNotifications
 *
 * 件名「【スマクラ】[(株)スタイリングライフ・ホールディングス]からの発注データ受信通知」
 * を検知 → Slack #-pj-2foods-order に投稿
 *
 * 設定値（GAS のスクリプトプロパティで管理）:
 *   - SLACK_BOT_TOKEN: xoxb-... (Slack Bot User OAuth Token)
 *   - SLACK_CHANNEL: #-pj-2foods-order
 *   - SLACK_MENTIONS: <!channel> <@U_kana_ito_id>  ※実際のユーザーIDで置換
 *
 * 重複防止: 処理済みメールに Gmail ラベル "smacla-notified" を付与
 */

const PROP = PropertiesService.getScriptProperties();

const SUBJECT_PATTERN = "【スマクラ】";
const SENDER_PATTERN = "smcl@cs-cxchange.net";
// 直近N日のメールのみ対象（古いメールに反応しないように）
const SEARCH_DAYS = 3;
// 既に通知済みのメールに付けるラベル
const PROCESSED_LABEL = "smacla-notified";

/**
 * 5分おきトリガーで呼び出される本体
 */
function processSmaclaNotifications() {
  const slackToken = PROP.getProperty("SLACK_BOT_TOKEN");
  const slackChannel = PROP.getProperty("SLACK_CHANNEL") || "#-pj-2foods-order";
  const slackMentions = PROP.getProperty("SLACK_MENTIONS") || "<!channel>";

  if (!slackToken) {
    console.error("SLACK_BOT_TOKEN が未設定です。スクリプトプロパティに登録してください。");
    return;
  }

  const label = getOrCreateLabel(PROCESSED_LABEL);

  // 検索: 直近N日 / 件名一致 / 未処理（ラベル無し）
  const query = `subject:"${SUBJECT_PATTERN}" from:${SENDER_PATTERN} newer_than:${SEARCH_DAYS}d -label:${PROCESSED_LABEL}`;
  const threads = GmailApp.search(query, 0, 20);

  if (threads.length === 0) {
    console.log("新規のスマクラ通知メールなし");
    return;
  }

  console.log(`新規 ${threads.length} 件のスマクラ通知を処理`);

  let processedCount = 0;
  for (const thread of threads) {
    const messages = thread.getMessages();
    for (const msg of messages) {
      // 既に処理済みラベル付きの場合スキップ（念のため）
      const threadLabels = thread.getLabels().map(l => l.getName());
      if (threadLabels.includes(PROCESSED_LABEL)) continue;

      // メール本文から情報抽出
      const info = extractMailInfo(msg);
      if (!info) {
        console.warn(`メール解析失敗: ${msg.getSubject()}`);
        continue;
      }

      // Slack に投稿
      const ok = postSlack(slackToken, slackChannel, slackMentions, info);
      if (ok) {
        processedCount++;
      }
    }
    // スレッドにラベル付与（同一スレッド内のメールはまとめて処理済みに）
    thread.addLabel(label);
  }

  console.log(`完了: ${processedCount} 件 Slack 通知済み`);
}

/**
 * メール本文から URL・ログインID・受信時刻を抽出
 */
function extractMailInfo(message) {
  const body = message.getPlainBody();
  const date = message.getDate();

  // URL 抽出（https://smcl.cs-cxchange.net/...）
  const urlMatch = body.match(/https:\/\/smcl\.cs-cxchange\.net\/[^\s]*/);
  const url = urlMatch ? urlMatch[0] : "https://smcl.cs-cxchange.net/smcl2/Login.aspx";

  // ログインID 抽出（例: 「ログインID：002582」）
  const idMatch = body.match(/ログインＩＤ[：:]\s*(\S+)/);
  const loginId = idMatch ? idMatch[1] : "(取得失敗)";

  // 小売名（例: 「小売名：(株)スタイリングライフ・ホールディングス」）
  const retailerMatch = body.match(/小売名[：:]\s*(.+)/);
  const retailer = retailerMatch ? retailerMatch[1].trim() : "";

  return {
    subject: message.getSubject(),
    date: Utilities.formatDate(date, "Asia/Tokyo", "yyyy-MM-dd HH:mm"),
    url: url,
    loginId: loginId,
    retailer: retailer,
    messageId: message.getId(),
  };
}

/**
 * Slack chat.postMessage 投稿
 */
function postSlack(token, channel, mentions, info) {
  const text =
    `${mentions}\n` +
    `📦 *スマクラ受注通知 受信*\n` +
    `受信時刻: ${info.date}\n` +
    (info.retailer ? `小売: ${info.retailer}\n` : '') +
    `🔗 ${info.url}\n` +
    `ログインID: \`${info.loginId}\`\n\n` +
    `⚠️ CSVをダウンロードして FAX受注システムに取り込んでください。`;

  const payload = {
    channel: channel,
    text: text,
    // mrkdwn 表示
    unfurl_links: false,
  };

  const response = UrlFetchApp.fetch("https://slack.com/api/chat.postMessage", {
    method: "post",
    contentType: "application/json; charset=utf-8",
    headers: {
      Authorization: "Bearer " + token,
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  const status = response.getResponseCode();
  const body = JSON.parse(response.getContentText());
  if (status !== 200 || !body.ok) {
    console.error(`Slack投稿失敗 status=${status} error=${body.error || ""} response=${response.getContentText().substring(0, 200)}`);
    return false;
  }
  console.log(`Slack投稿成功: ts=${body.ts}`);
  return true;
}

/**
 * Gmail ラベル取得/作成
 */
function getOrCreateLabel(name) {
  let label = GmailApp.getUserLabelByName(name);
  if (!label) {
    label = GmailApp.createLabel(name);
    console.log(`ラベル作成: ${name}`);
  }
  return label;
}

/**
 * テスト用: 手動実行で1回チェック
 */
function testRun() {
  processSmaclaNotifications();
}

/**
 * テスト用: 強制1件投稿（ラベル状態問わず最新1件を Slack に投げる）
 */
function testForcePost() {
  const slackToken = PROP.getProperty("SLACK_BOT_TOKEN");
  const slackChannel = PROP.getProperty("SLACK_CHANNEL") || "#-pj-2foods-order";
  const slackMentions = PROP.getProperty("SLACK_MENTIONS") || "<!channel>";

  const query = `subject:"${SUBJECT_PATTERN}" from:${SENDER_PATTERN}`;
  const threads = GmailApp.search(query, 0, 1);
  if (threads.length === 0) {
    console.log("対象メールなし");
    return;
  }
  const msg = threads[0].getMessages()[0];
  const info = extractMailInfo(msg);
  if (info) {
    postSlack(slackToken, slackChannel, slackMentions, info);
  }
}
