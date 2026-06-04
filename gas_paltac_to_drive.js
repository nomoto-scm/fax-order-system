/**
 * PALTAC自動取込 GASスクリプト
 *
 * 目的: scmorderbox@two2.jp に届く PALTAC WEBEDI 発注メールを監視し、
 *       CSV添付を Google Drive の「_自動取込/新着」フォルダに自動保存する。
 *
 * 動作:
 *   - Gmail検索: subject:"【株式会社　PALTAC】WEBEDI" has:attachment -label:SCM/PALTAC自動取込済み
 *   - CSV添付を取得し、Driveに保存（eFaxと同じフォルダ。drive_processor.pyが拡張子で自動振り分け）
 *   - サイドカー .meta.json に source_channel='paltac' を記録
 *   - 処理済みメールには「SCM/PALTAC自動取込済み」ラベルを付与
 *
 * 設置方法:
 *   1. https://script.google.com/ を scmorderbox@two2.jp でログインして開く
 *   2. 「新しいプロジェクト」を作成（既存「eFax自動取込」とは別プロジェクトを推奨）
 *   3. 下記コードを貼り付け
 *   4. 初回実行: testSetup → runOnce
 *   5. トリガー設定: processPaltacEmails を5分おきに自動実行
 */

// ============================================================
// 設定
// ============================================================

/** Drive保存先フォルダID（eFaxと同じ _自動取込/新着） */
const DRIVE_FOLDER_ID = '107ZIFcg-u-eUq-gUlwkLqs7jf2oEbEaZ';

/** Slack Incoming Webhook URL（後で設定） */
const SLACK_WEBHOOK_URL = '';

/** PALTAC件名フィルタ（全角スペース注意） */
const SUBJECT_FILTER = '【株式会社　PALTAC】WEBEDI';

/** 処理済みラベル名 */
const PROCESSED_LABEL = 'SCM/PALTAC自動取込済み';

/** 今日以降のメールのみ処理 */
const SINCE_DATE = '2026/04/24';


// ============================================================
// メイン処理
// ============================================================

function processPaltacEmails() {
  try {
    const folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
    const label = getOrCreateLabel(PROCESSED_LABEL);

    // 件名で PALTAC を識別。-label でラベル済を除外。
    const query = `subject:"${SUBJECT_FILTER}" has:attachment -label:${PROCESSED_LABEL} after:${SINCE_DATE}`;
    const threads = GmailApp.search(query, 0, 50);

    if (threads.length === 0) {
      console.log('処理対象メールなし');
      return;
    }

    let savedCount = 0;
    let errorCount = 0;

    for (const thread of threads) {
      const messages = thread.getMessages();
      let threadHadError = false;
      for (const msg of messages) {
        // 件名にPALTACキーワードがないメッセージはスキップ
        if (msg.getSubject().indexOf('PALTAC') === -1) continue;

        try {
          const result = saveAttachments(msg, folder);
          savedCount += result.saved;
        } catch (err) {
          errorCount++;
          threadHadError = true;
          console.error(`メッセージ処理失敗: ${msg.getSubject()} - ${err}`);
          notifySlack(`:warning: PALTAC取込エラー\n件名: ${msg.getSubject()}\nエラー: ${err.message || err}`);
        }
      }
      // エラーなしの場合のみラベル付与（エラー時は次回自動リトライ）
      if (!threadHadError) thread.addLabel(label);
    }

    const logMsg = `処理完了: ${threads.length}スレッド / CSV保存${savedCount}件 / エラー${errorCount}件`;
    console.log(logMsg);
    if (savedCount > 0) {
      notifySlack(`:inbox_tray: PALTAC取込 ${savedCount}件を Drive に保存しました (${new Date().toLocaleString('ja-JP')})`);
    }

  } catch (err) {
    console.error(`全体エラー: ${err}`);
    notifySlack(`:rotating_light: PALTAC取込スクリプト全体エラー\n${err.message || err}`);
  }
}


// ============================================================
// 添付ファイル保存（CSVとサイドカー.meta.jsonを保存）
// ============================================================

function saveAttachments(msg, folder) {
  let saved = 0;
  const attachments = msg.getAttachments();
  const messageId = msg.getId();
  const receivedAt = msg.getDate();
  const dateStr = Utilities.formatDate(receivedAt, 'Asia/Tokyo', 'yyyyMMdd_HHmmss');

  for (const att of attachments) {
    // CSVのみ対象（content-typeでもファイル名でも判定）
    const ct = att.getContentType() || '';
    const name = att.getName() || '';
    const isCsv = ct.includes('csv') || /\.csv$/i.test(name);
    if (!isCsv) continue;

    // ファイル名: paltac_{受信日時}_{msgIdShort}_{元ファイル名}.csv
    const msgIdShort = messageId.substring(0, 8);
    const origName = name.replace(/\.csv$/i, '').replace(/[\\/:*?"<>|]/g, '_');
    const fileName = `paltac_${dateStr}_${msgIdShort}_${origName}.csv`;

    // 重複チェック（同名ファイル）
    const existing = folder.getFilesByName(fileName);
    if (existing.hasNext()) {
      console.log(`既存ファイルのためスキップ: ${fileName}`);
      continue;
    }

    // Driveに保存
    const file = folder.createFile(att.copyBlob().setName(fileName));

    // メタ情報
    const meta = {
      source_channel: 'paltac',
      gmail_message_id: messageId,
      gmail_thread_id: msg.getThread().getId(),
      from: msg.getFrom(),
      subject: msg.getSubject(),
      received_at: receivedAt.toISOString(),
      saved_at: new Date().toISOString(),
      file_name: fileName,
    };
    const metaJson = JSON.stringify(meta, null, 2);
    file.setDescription(metaJson);

    // サイドカー .meta.json
    const metaFileName = fileName.replace(/\.csv$/i, '.meta.json');
    const metaExisting = folder.getFilesByName(metaFileName);
    if (!metaExisting.hasNext()) {
      folder.createFile(metaFileName, metaJson, 'application/json');
    }

    console.log(`保存: ${fileName} (+ .meta.json)`);
    saved++;
  }

  return { saved };
}


// ============================================================
// ユーティリティ
// ============================================================

function getOrCreateLabel(name) {
  let label = GmailApp.getUserLabelByName(name);
  if (!label) label = GmailApp.createLabel(name);
  return label;
}

function notifySlack(text) {
  if (!SLACK_WEBHOOK_URL) {
    console.log('Slack未設定のためスキップ:', text);
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
// テスト用関数
// ============================================================

function testSetup() {
  console.log('=== PALTAC テスト開始 ===');

  try {
    const folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
    console.log(`✅ Driveフォルダ接続OK: ${folder.getName()}`);
  } catch (err) {
    console.error(`❌ Driveフォルダ接続失敗: ${err}`);
    return;
  }

  try {
    const query = `subject:"${SUBJECT_FILTER}"`;
    const threads = GmailApp.search(query, 0, 5);
    console.log(`✅ Gmail検索OK: PALTAC発注メールが ${threads.length} 件見つかりました（過去分含む）`);
    if (threads.length > 0) {
      const m = threads[0].getMessages()[0];
      console.log(`   最新: ${m.getSubject()} (${Utilities.formatDate(m.getDate(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm')})`);
    }
  } catch (err) {
    console.error(`❌ Gmail検索失敗: ${err}`);
    return;
  }

  if (SLACK_WEBHOOK_URL) {
    notifySlack(':white_check_mark: PALTAC自動取込スクリプト 設定テスト成功 (testSetup 実行)');
    console.log('✅ Slack通知テスト送信完了（チャネルを確認してください）');
  } else {
    console.log('⚠️  Slack未設定（SLACK_WEBHOOK_URL が空）。通知スキップ');
  }

  console.log('=== テスト完了 ===');
}

function runOnce() {
  processPaltacEmails();
}
