import streamlit as st
import os
import logging
from PIL import Image
import pillow_avif  # AVIF プラグイン登録
from io import BytesIO
import time

# --- ログ設定 ---
logger = logging.getLogger('avif_converter')
logger.setLevel(logging.INFO)
# 既存のハンドラーをクリアして重複出力を防止
if logger.hasHandlers():
    logger.handlers.clear()
fh = logging.FileHandler('conversion.log', encoding='utf-8')
fh.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
logger.addHandler(fh)

st.title("AVIF 変換アプリ (動的圧縮率調整)")

# プラグイン読み込み確認
try:
    import pillow_avif
    st.success("AVIF プラグインが正常に読み込まれました。")
except ImportError:
    st.error(
        "AVIF プラグインが読み込まれていません。\\n"
        "pip install pillow-avif-plugin を実行し、\\n"
        "スクリプト冒頭に import pillow_avif を追加してください。"
    )
    st.stop()

# UI入力
st.sidebar.header("変換設定")
directory = st.sidebar.text_input("変換対象フォルダのパス", "")
target_kb = st.sidebar.number_input("目標ファイルサイズ (KB)", min_value=1, value=100)
tolerance_kb = st.sidebar.number_input("許容誤差 (KB)", min_value=0, value=5)
max_iterations = st.sidebar.slider("最大バイナリサーチ回数", 5, 15, 10)
output_subfolder = st.sidebar.text_input("出力サブフォルダ名", "AVIF出力")

# 詳細設定
show_advanced = st.sidebar.checkbox("詳細設定を表示")
if show_advanced:
    quality_mode = st.sidebar.selectbox("品質モード", ["MSE", "SSIM"], index=0)
    keep_original_name = st.sidebar.checkbox("元ファイル名を維持", value=False)
    recursive_search = st.sidebar.checkbox("サブフォルダも処理", value=True)
    ignore_hidden = st.sidebar.checkbox("隠しファイルを無視", value=True)
else:
    quality_mode = "MSE"
    keep_original_name = False
    recursive_search = True
    ignore_hidden = True

# ヘルプ情報
with st.sidebar.expander("ヘルプ"):
    st.markdown("""
    - **目標ファイルサイズ**: 変換後のAVIFファイルの目標サイズをKB単位で指定
    - **許容誤差**: 目標サイズからの許容される差分をKB単位で指定
    - **品質モード**:
        - MSE: 平均二乗誤差ベース（視覚的な違いを最小化）
        - SSIM: 構造的類似性ベース（構造的な違いを最小化）
    - **最大バイナリサーチ回数**: 最適な品質を見つけるための最大試行回数
    - **隠しファイルを無視**: .から始まるファイルを処理対象から除外
    """)

# 隠しファイルかどうかを判定する関数
def is_hidden(filepath):
    """ファイルが隠しファイルかどうかを判定"""
    return os.path.basename(filepath).startswith('.')

# バイナリサーチで最適な品質を決定する関数
def find_optimal_quality(img_path, target_bytes, tol_bytes, max_iter=10, q_mode="MSE"):
    low, high = 1, 100
    best_quality = None
    best_size = None
    iteration = 0

    img = Image.open(img_path)

    while low <= high and iteration < max_iter:
        mid = (low + high) // 2
        iteration += 1

        buffer = BytesIO()
        try:
            img.save(buffer, format='AVIF', quality_mode=q_mode, quality=mid)
            current_size = buffer.tell()

            # サイズ情報を記録
            if best_quality is None or abs(current_size - target_bytes) < abs(best_size - target_bytes):
                best_quality = mid
                best_size = current_size

            # 許容範囲内ならそれを返す
            if abs(current_size - target_bytes) <= tol_bytes:
                return {
                    'quality': mid,
                    'size': current_size,
                    'iterations': iteration,
                    'in_tolerance': True
                }

            if current_size > target_bytes:
                high = mid - 1
            else:
                low = mid + 1

        except Exception as e:
            logger.error(f"品質{mid}での変換エラー: {str(e)}")
            # エラーが発生した場合は範囲を狭める
            high = mid - 1

    # 最適な品質を返す
    return {
        'quality': best_quality,
        'size': best_size,
        'iterations': iteration,
        'in_tolerance': False if best_size is None else abs(best_size - target_bytes) <= tol_bytes
    }

# ファイルサイズをフォーマットする関数
def format_size(size_bytes):
    """ファイルサイズを読みやすい形式に変換"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.1f} KB"
    else:
        return f"{size_bytes/(1024*1024):.2f} MB"

# 変換処理のメイン関数
def convert_to_avif():
    if not os.path.isdir(directory):
        st.error("有効なディレクトリを指定してください。")
        logger.error(f"無効なディレクトリ: {directory}")
        return

    # ログファイルをクリア
    open('conversion.log', 'w', encoding='utf-8').close()
    logger.info(f"処理開始: {directory} [目標={target_kb}KB, 許容誤差={tolerance_kb}KB, 品質モード={quality_mode}]")

    # 出力ディレクトリの設定
    base_out = os.path.join(directory, output_subfolder)

    # 処理対象ファイルの収集
    file_paths = []
    if recursive_search:
        for root, dirs, files in os.walk(directory):
            # 出力ディレクトリ自体は除外
            if os.path.normpath(root) == os.path.normpath(base_out):
                continue

            # 隠しディレクトリをスキップ（オプション）
            if ignore_hidden and os.path.basename(root).startswith('.'):
                continue

            for fname in files:
                # 隠しファイルをスキップ（オプション）
                if ignore_hidden and fname.startswith('.'):
                    continue

                if fname.lower().endswith(('png', 'jpg', 'jpeg', 'webp', 'bmp')):
                    file_path = os.path.join(root, fname)
                    file_paths.append(file_path)
    else:
        # 非再帰的に現在のディレクトリのみ処理
        for fname in os.listdir(directory):
            # 隠しファイルをスキップ（オプション）
            if ignore_hidden and fname.startswith('.'):
                continue

            if fname.lower().endswith(('png', 'jpg', 'jpeg', 'webp', 'bmp')):
                file_paths.append(os.path.join(directory, fname))

    total = len(file_paths)
    if total == 0:
        st.warning("対象画像が見つかりませんでした。")
        logger.warning("対象ファイルなし")
        return

    # 統計情報の初期化
    stats = {
        'converted': 0,
        'failed': 0,
        'original_size_total': 0,
        'converted_size_total': 0,
        'start_time': time.time()
    }

    # 進捗バーの設定
    progress_bar = st.progress(0)
    status_text = st.empty()

    # 統計表示用のコンテナ
    stats_container = st.container()

    # 変換処理
    target_bytes = target_kb * 1024
    tol_bytes = tolerance_kb * 1024

    for idx, path in enumerate(file_paths, 1):
        # 相対パスの計算
        rel = os.path.relpath(path, directory)
        rel_dir = os.path.dirname(rel)
        out_dir = os.path.join(base_out, rel_dir)
        os.makedirs(out_dir, exist_ok=True)

        # 入力ファイルのサイズ取得
        original_size = os.path.getsize(path)
        stats['original_size_total'] += original_size

        # 出力ファイル名の決定
        if keep_original_name:
            avif_path = os.path.join(out_dir, os.path.basename(path))
            # 拡張子がAVIFでない場合は変更
            if not avif_path.lower().endswith('.avif'):
                avif_path = os.path.splitext(avif_path)[0] + ".avif"
        else:
            avif_path = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".avif")

        # 状態更新
        status_text.text(f"処理中: {idx}/{total} - {os.path.basename(path)}")

        try:
            # 最適な品質を検索
            result = find_optimal_quality(path, target_bytes, tol_bytes, max_iterations, quality_mode)

            if result['quality'] is None:
                logger.error(f"最適品質検索失敗: {path}")
                stats['failed'] += 1
                continue

            # 変換実行
            img = Image.open(path)
            img.save(avif_path, format="AVIF", quality_mode=quality_mode, quality=result['quality'])

            # 変換後のサイズを取得
            converted_size = os.path.getsize(avif_path)
            stats['converted_size_total'] += converted_size

            # 圧縮率を計算
            compression_ratio = (1 - (converted_size / original_size)) * 100 if original_size > 0 else 0

            logger.info(
                f"変換成功: {path} -> {avif_path} "
                f"(品質={result['quality']}, サイズ: {format_size(original_size)} -> {format_size(converted_size)}, "
                f"圧縮率: {compression_ratio:.1f}%, 反復: {result['iterations']})"
            )

            stats['converted'] += 1

        except Exception as e:
            logger.error(f"変換失敗: {path} -> {str(e)}")
            stats['failed'] += 1

        # 進捗更新
        progress_bar.progress(idx / total)

    # 処理時間の計算
    elapsed_time = time.time() - stats['start_time']

    # 結果表示
    with stats_container:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("変換結果")
            st.markdown(f"✅ **変換成功**: {stats['converted']}/{total} ファイル")
            if stats['failed'] > 0:
                st.markdown(f"❌ **変換失敗**: {stats['failed']} ファイル")
            st.markdown(f"⏱️ **処理時間**: {elapsed_time:.1f} 秒")

        with col2:
            st.subheader("サイズ情報")
            if stats['original_size_total'] > 0:
                compression = (1 - (stats['converted_size_total'] / stats['original_size_total'])) * 100
                st.markdown(f"📊 **圧縮率**: {compression:.1f}%")
                st.markdown(f"📁 **元サイズ合計**: {format_size(stats['original_size_total'])}")
                st.markdown(f"📁 **変換後サイズ合計**: {format_size(stats['converted_size_total'])}")

    # 完了メッセージ
    status_text.success(f"変換完了: {stats['converted']}/{total} 件のファイルを変換しました")
    logger.info(
        f"完了: {stats['converted']}/{total} 件変換完了。"
        f"元サイズ合計: {format_size(stats['original_size_total'])}, "
        f"変換後合計: {format_size(stats['converted_size_total'])}, "
        f"全体圧縮率: {(1 - (stats['converted_size_total'] / stats['original_size_total'])) * 100:.1f}%"
    )

    # ログ表示
    if os.path.exists('conversion.log'):
        with st.expander("変換ログを表示"):
            st.text(open('conversion.log', encoding='utf-8').read())

# 実行ボタン
if st.button("変換開始", type="primary"):
    convert_to_avif()
