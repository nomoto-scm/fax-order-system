/**
 * eFax自動取込 GASスクリプト
 *
 * 目的: 2foods-scm@two2.jp に届く eFax メール（from: message@inbound.efax.com）を監視し、
 *       PDF添付を Google Drive の「_自動取込/新着」フォルダに自動保存する。
 *
 * 設置方法:
 *   1. https://script.google.com/ を 2foods-scm@two2.jp でログインして開く
 *   2. 「新しいプロジェクト」を作成
 *   3. 下記コードを貼り付け
 *   4. SLACK_WEBHOOK_URL を実際の値に書き換え（未設定でも動作。Slack通知のみスキップ）
 *   5. 初回実行: 関数選択 [testSetup] → [実行] ボタン
 *      → Google認証ダイアログが出るので許可
 *      → 実行ログで接続確認
 *   6. トリガー設定: 左メニュー [時計アイコン] → [トリガーを追加]
 *      - 実行する関数: processEfaxEmails
 *      - イベントのソース: 時間主導型
 *      - トリガーのタイプ: 分ベースのタイマー → 5分おき
 *
 * 動作ロジック:
 *   - Gmail検索: from:message@inbound.efax.com has:attachment -label:SCM/自動取込済み
 *   - PDF添付を取得し、Driveに保存
 *   - ファイル説明欄にGmailメタ情報（message_id, from, subject, received_at）をJSON形式で保存
 *   - 処理済みメールには「SCM/自動取込済み」ラベルを付与（次回スキャンから除外）
 *   - エラー時はSlack通知（設定時のみ）
 */

// ============================================================
// 設定
// ============================================================

/** Drive保存先フォルダID（_自動取込/新着 のID） */
const DRIVE_FOLDER_ID = '107ZIFcg-u-eUq-gUlwkLqs7jf2oEbEaZ';

/** Slack Incoming Webhook URL（未設定なら空文字列のまま。Slack通知のみスキップ） */
const SLACK_WEBHOOK_URL = '';

/** eFax送信元アドレス（変更不要） */
const EFAX_SENDER = 'message@inbound.efax.com';

/** 処理済みラベル名（変更不要） */
const PROCESSED_LABEL = 'SCM/自動取込済み';


// ============================================================
// メイン処理
// ============================================================

function processEfaxEmails() {
  try {
    const folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
    const label = getOrCreateLabel(PROCESSED_LABEL);

    // 未処理のeFaxメールを検索（最大50スレッド/回）
    // 今日以降のメールのみ処理（古いFAXは印刷フローで処理済みのため取込不要）
    const SINCE_DATE = '2026/04/24';
    const query = `from:${EFAX_SENDER} has:attachment -label:${PROCESSED_LABEL} after:${SINCE_DATE}`;
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
        // 差出人がeFaxでないメッセージはスキップ（スレッドに他のメッセージが混ざっている場合）
        if (!msg.getFrom().includes(EFAX_SENDER)) continue;

        try {
          const result = saveAttachments(msg, folder);
          savedCount += result.saved;
        } catch (err) {
          errorCount++;
          threadHadError = true;
          console.error(`メッセージ処理失敗: ${msg.getSubject()} - ${err}`);
          notifySlack(`:warning: eFax取込エラー\n件名: ${msg.getSubject()}\nエラー: ${err.message || err}`);
        }
      }
      // エラーなしの場合のみラベル付与（エラー時は次回実行で自動リトライ）
      if (!threadHadError) thread.addLabel(label);
    }

    const logMsg = `処理完了: ${threads.length}スレッド / PDF保存${savedCount}件 / エラー${errorCount}件`;
    console.log(logMsg);
    if (savedCount > 0) {
      notifySlack(`:inbox_tray: eFax取込 ${savedCount}件を Drive に保存しました (${new Date().toLocaleString('ja-JP')})`);
    }

  } catch (err) {
    console.error(`全体エラー: ${err}`);
    notifySlack(`:rotating_light: eFax取込スクリプト全体エラー\n${err.message || err}`);
  }
}


// ============================================================
// 添付ファイル保存
// ============================================================

function saveAttachments(msg, folder) {
  let saved = 0;
  const attachments = msg.getAttachments();
  const messageId = msg.getId();
  const receivedAt = msg.getDate();
  const dateStr = Utilities.formatDate(receivedAt, 'Asia/Tokyo', 'yyyyMMdd_HHmmss');

  for (const att of attachments) {
    // PDFのみ対象
    if (att.getContentType() !== 'application/pdf') continue;

    // ファイル名に受信日時とmessage_id先頭8桁を付与（重複防止）
    const msgIdShort = messageId.substring(0, 8);
    const origName = att.getName().replace(/\.pdf$/i, '');
    const fileName = `efax_${dateStr}_${msgIdShort}_${origName}.pdf`;

    // 既にDriveに同名ファイルがあるかチェック（重複防止）
    const existing = folder.getFilesByName(fileName);
    if (existing.hasNext()) {
      console.log(`既存ファイルのためスキップ: ${fileName}`);
      continue;
    }

    // Driveに保存
    const file = folder.createFile(att.copyBlob().setName(fileName));

    // メタ情報を作成
    const meta = {
      source_channel: 'efax',
      gmail_message_id: messageId,
      gmail_thread_id: msg.getThread().getId(),
      from: msg.getFrom(),
      subject: msg.getSubject(),
      received_at: receivedAt.toISOString(),
      saved_at: new Date().toISOString(),
      pdf_file_name: fileName,
    };
    const metaJson = JSON.stringify(meta, null, 2);

    // ① Driveファイル説明欄にも保存（Drive API経由で読みたい場合用）
    file.setDescription(metaJson);

    // ② サイドカー .meta.json ファイルとして保存（drive_processor.pyがファイルシステムから直接読む）
    const metaFileName = fileName.replace(/\.pdf$/i, '.meta.json');
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

/**
 * 設定の疎通確認（Drive と Slack がつながっているか確認）
 * 初回実行時にGoogle認証ダイアログが出るので許可してください
 */
function testSetup() {
  console.log('=== テスト開始 ===');

  // Drive接続テスト
  try {
    const folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
    console.log(`✅ Driveフォルダ接続OK: ${folder.getName()}`);
  } catch (err) {
    console.error(`❌ Driveフォルダ接続失敗: ${err}`);
    return;
  }

  // Gmail検索テスト
  try {
    const query = `from:${EFAX_SENDER}`;
    const threads = GmailApp.search(query, 0, 5);
    console.log(`✅ Gmail検索OK: eFaxメールが ${threads.length} 件見つかりました（過去分含む）`);
    if (threads.length > 0) {
      console.log(`   最新: ${threads[0].getMessages()[0].getSubject()}`);
    }
  } catch (err) {
    console.error(`❌ Gmail検索失敗: ${err}`);
    return;
  }

  // Slack通知テスト
  if (SLACK_WEBHOOK_URL) {
    notifySlack(':white_check_mark: eFax自動取込スクリプト 設定テスト成功 (testSetup 実行)');
    console.log('✅ Slack通知テスト送信完了（チャネルを確認してください）');
  } else {
    console.log('⚠️  Slack未設定（SLACK_WEBHOOK_URL が空）。通知スキップ');
  }

  console.log('=== テスト完了 ===');
}

/**
 * 【要注意】メイン処理を手動で1回実行する（本番と同じ動作）
 * これを実行すると、未処理のeFaxメールがDriveに保存され、ラベルが付きます
 */
function runOnce() {
  processEfaxEmails();
}
