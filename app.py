import streamlit as st
import os
import logging
from PIL import Image
import pillow_avif  # AVIF プラグイン登録
from io import BytesIO
import time
import zipfile
import tempfile
import shutil

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
        "AVIF プラグインが読み込まれていません。\n"
        "pip install pillow-avif-plugin を実行し、\n"
        "スクリプト冒頭に import pillow_avif を追加してください。"
    )
    st.stop()

# モード選択
st.sidebar.header("動作モード")
app_mode = st.sidebar.radio(
    "動作モードを選択してください",
    ["ウェブモード (ファイルアップロード)", "ローカルモード (フォルダ指定)"],
    index=0
)

# 共通設定
st.sidebar.header("変換設定")
target_kb = st.sidebar.number_input("目標ファイルサイズ (KB)", min_value=1, value=100)
tolerance_kb = st.sidebar.number_input("許容誤差 (KB)", min_value=0, value=5)
max_iterations = st.sidebar.slider("最大バイナリサーチ回数", 5, 15, 10)

# 詳細設定
show_advanced = st.sidebar.checkbox("詳細設定を表示")
if show_advanced:
    quality_mode = st.sidebar.selectbox("品質モード", ["MSE", "SSIM"], index=0)
    keep_original_name = st.sidebar.checkbox("元ファイル名を維持", value=False)
else:
    quality_mode = "MSE"
    keep_original_name = False

# モード別の入力UI
if app_mode == "ウェブモード (ファイルアップロード)":
    st.header("📁 ファイルアップロード")
    
    # アップロード方法の選択
    upload_method = st.radio(
        "アップロード方法を選択してください",
        ["個別ファイル", "ZIPファイル"],
        horizontal=True
    )
    
    uploaded_files = []
    
    if upload_method == "個別ファイル":
        uploaded_files = st.file_uploader(
            "変換したい画像ファイルを選択してください（複数選択可能）",
            type=['png', 'jpg', 'jpeg', 'webp', 'bmp'],
            accept_multiple_files=True,
            help="PNG, JPG, JPEG, WebP, BMPファイルに対応しています"
        )
    else:
        zip_file = st.file_uploader(
            "画像ファイルが含まれたZIPファイルを選択してください",
            type=['zip'],
            help="ZIPファイル内の画像ファイル（PNG, JPG, JPEG, WebP, BMP）を変換します"
        )
        
        if zip_file:
            # ZIPファイルの内容を展開してファイルリストを作成
            try:
                with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                    file_list = zip_ref.namelist()
                    image_files = [f for f in file_list if f.lower().endswith(('png', 'jpg', 'jpeg', 'webp', 'bmp'))]
                    
                    if image_files:
                        st.info(f"ZIPファイル内に {len(image_files)} 個の画像ファイルが見つかりました")
                        with st.expander("ファイル一覧を表示"):
                            for img_file in image_files:
                                st.text(f"📷 {img_file}")
                        
                        # ZIPファイルから画像ファイルを読み込み
                        uploaded_files = []
                        for img_file in image_files:
                            try:
                                file_data = zip_ref.read(img_file)
                                # BytesIOオブジェクトを作成してファイル情報を付加
                                file_obj = BytesIO(file_data)
                                file_obj.name = os.path.basename(img_file)
                                uploaded_files.append(file_obj)
                            except Exception as e:
                                st.warning(f"ファイル '{img_file}' の読み込みに失敗しました: {str(e)}")
                    else:
                        st.warning("ZIPファイル内に対応する画像ファイルが見つかりませんでした")
            except Exception as e:
                st.error(f"ZIPファイルの処理中にエラーが発生しました: {str(e)}")

else:  # ローカルモード
    st.header("📂 ローカルフォルダ指定")
    directory = st.text_input("変換対象フォルダのパス", "")
    output_subfolder = st.text_input("出力サブフォルダ名", "AVIF出力")
    
    if show_advanced:
        recursive_search = st.checkbox("サブフォルダも処理", value=True)
        ignore_hidden = st.checkbox("隠しファイルを無視", value=True)
    else:
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
    - **ウェブモード**: ファイルをアップロードして変換
    - **ローカルモード**: ローカルのフォルダを指定して変換
    """)

# バイナリサーチで最適な品質を決定する関数
def find_optimal_quality(img, target_bytes, tol_bytes, max_iter=10, q_mode="MSE"):
    low, high = 1, 100
    best_quality = None
    best_size = None
    iteration = 0

    while low <= high and iteration < max_iter:
        mid = (low + high) // 2
        iteration += 1

        buffer = BytesIO()
        try:
            # 画像をコピーして保存
            img_copy = img.copy()
            img_copy.save(buffer, format='AVIF', quality_mode=q_mode, quality=mid)
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
            high = mid - 1

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

# 隠しファイルかどうかを判定する関数
def is_hidden(filepath):
    """ファイルが隠しファイルかどうかを判定"""
    return os.path.basename(filepath).startswith('.')

# ウェブモードでの変換処理
def convert_uploaded_files(uploaded_files):
    if not uploaded_files:
        st.error("変換するファイルを選択してください。")
        return

    # ログファイルをクリア
    open('conversion.log', 'w', encoding='utf-8').close()
    logger.info(f"ウェブモード処理開始: {len(uploaded_files)}ファイル [目標={target_kb}KB, 許容誤差={tolerance_kb}KB, 品質モード={quality_mode}]")

    total = len(uploaded_files)
    target_bytes = target_kb * 1024
    tol_bytes = tolerance_kb * 1024

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
    stats_container = st.container()

    # 変換結果を保存するリスト
    converted_files = []

    for idx, uploaded_file in enumerate(uploaded_files, 1):
        # 元のファイルサイズを取得
        if hasattr(uploaded_file, 'size'):
            original_size = uploaded_file.size
        else:
            # BytesIOの場合はシークして取得
            current_pos = uploaded_file.tell()
            uploaded_file.seek(0, 2)  # ファイル末尾へ
            original_size = uploaded_file.tell()
            uploaded_file.seek(current_pos)  # 元の位置に戻す

        stats['original_size_total'] += original_size

        # ファイル名の処理
        original_name = getattr(uploaded_file, 'name', f'image_{idx}')
        if keep_original_name:
            avif_name = original_name
            if not avif_name.lower().endswith('.avif'):
                avif_name = os.path.splitext(avif_name)[0] + ".avif"
        else:
            avif_name = os.path.splitext(original_name)[0] + ".avif"

        # 状態更新
        status_text.text(f"処理中: {idx}/{total} - {original_name}")

        try:
            # 画像を開く
            img = Image.open(uploaded_file)
            
            # 最適な品質を検索
            result = find_optimal_quality(img, target_bytes, tol_bytes, max_iterations, quality_mode)

            if result['quality'] is None:
                logger.error(f"最適品質検索失敗: {original_name}")
                stats['failed'] += 1
                continue

            # AVIF形式で変換
            buffer = BytesIO()
            img.save(buffer, format="AVIF", quality_mode=quality_mode, quality=result['quality'])
            
            converted_size = buffer.tell()
            stats['converted_size_total'] += converted_size

            # 圧縮率を計算
            compression_ratio = (1 - (converted_size / original_size)) * 100 if original_size > 0 else 0

            # 変換結果を保存
            buffer.seek(0)
            converted_files.append({
                'name': avif_name,
                'data': buffer.getvalue(),
                'original_size': original_size,
                'converted_size': converted_size,
                'compression_ratio': compression_ratio,
                'quality': result['quality'],
                'iterations': result['iterations']
            })

            logger.info(
                f"変換成功: {original_name} -> {avif_name} "
                f"(品質={result['quality']}, サイズ: {format_size(original_size)} -> {format_size(converted_size)}, "
                f"圧縮率: {compression_ratio:.1f}%, 反復: {result['iterations']})"
            )

            stats['converted'] += 1

        except Exception as e:
            logger.error(f"変換失敗: {original_name} -> {str(e)}")
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

    # ダウンロード機能
    if converted_files:
        st.subheader("📥 ダウンロード")
        
        if len(converted_files) == 1:
            # 単一ファイルの場合は直接ダウンロード
            file_info = converted_files[0]
            st.download_button(
                label=f"📷 {file_info['name']} をダウンロード",
                data=file_info['data'],
                file_name=file_info['name'],
                mime="image/avif"
            )
        else:
            # 複数ファイルの場合はZIPで一括ダウンロード
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for file_info in converted_files:
                    zip_file.writestr(file_info['name'], file_info['data'])
            
            zip_buffer.seek(0)
            st.download_button(
                label=f"📦 すべてのファイルをZIPでダウンロード ({len(converted_files)}ファイル)",
                data=zip_buffer.getvalue(),
                file_name="converted_avif_files.zip",
                mime="application/zip"
            )

        # 個別ファイル詳細情報
        if show_advanced:
            with st.expander("個別ファイル詳細"):
                for file_info in converted_files:
                    st.markdown(f"**{file_info['name']}**")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.text(f"品質: {file_info['quality']}")
                        st.text(f"反復: {file_info['iterations']}")
                    with col2:
                        st.text(f"元サイズ: {format_size(file_info['original_size'])}")
                        st.text(f"変換後: {format_size(file_info['converted_size'])}")
                    with col3:
                        st.text(f"圧縮率: {file_info['compression_ratio']:.1f}%")
                    st.markdown("---")

    # 完了メッセージ
    status_text.success(f"変換完了: {stats['converted']}/{total} 件のファイルを変換しました")
    logger.info(
        f"ウェブモード完了: {stats['converted']}/{total} 件変換完了。"
        f"元サイズ合計: {format_size(stats['original_size_total'])}, "
        f"変換後合計: {format_size(stats['converted_size_total'])}, "
        f"全体圧縮率: {(1 - (stats['converted_size_total'] / stats['original_size_total'])) * 100:.1f}%"
    )

    # ログ表示
    if os.path.exists('conversion.log'):
        with st.expander("変換ログを表示"):
            st.text(open('conversion.log', encoding='utf-8').read())

# ローカルモードでの変換処理（元の関数を維持）
def convert_local_directory():
    if not os.path.isdir(directory):
        st.error("有効なディレクトリを指定してください。")
        logger.error(f"無効なディレクトリ: {directory}")
        return

    # ログファイルをクリア
    open('conversion.log', 'w', encoding='utf-8').close()
    logger.info(f"ローカルモード処理開始: {directory} [目標={target_kb}KB, 許容誤差={tolerance_kb}KB, 品質モード={quality_mode}]")

    # 出力ディレクトリの設定
    base_out = os.path.join(directory, output_subfolder)

    # 処理対象ファイルの収集
    file_paths = []
    if recursive_search:
        for root, dirs, files in os.walk(directory):
            if os.path.normpath(root) == os.path.normpath(base_out):
                continue

            if ignore_hidden and os.path.basename(root).startswith('.'):
                continue

            for fname in files:
                if ignore_hidden and fname.startswith('.'):
                    continue

                if fname.lower().endswith(('png', 'jpg', 'jpeg', 'webp', 'bmp')):
                    file_path = os.path.join(root, fname)
                    file_paths.append(file_path)
    else:
        for fname in os.listdir(directory):
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
    stats_container = st.container()

    # 変換処理
    target_bytes = target_kb * 1024
    tol_bytes = tolerance_kb * 1024

    for idx, path in enumerate(file_paths, 1):
        rel = os.path.relpath(path, directory)
        rel_dir = os.path.dirname(rel)
        out_dir = os.path.join(base_out, rel_dir)
        os.makedirs(out_dir, exist_ok=True)

        original_size = os.path.getsize(path)
        stats['original_size_total'] += original_size

        if keep_original_name:
            avif_path = os.path.join(out_dir, os.path.basename(path))
            if not avif_path.lower().endswith('.avif'):
                avif_path = os.path.splitext(avif_path)[0] + ".avif"
        else:
            avif_path = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".avif")

        status_text.text(f"処理中: {idx}/{total} - {os.path.basename(path)}")

        try:
            img = Image.open(path)
            result = find_optimal_quality(img, target_bytes, tol_bytes, max_iterations, quality_mode)

            if result['quality'] is None:
                logger.error(f"最適品質検索失敗: {path}")
                stats['failed'] += 1
                continue

            img.save(avif_path, format="AVIF", quality_mode=quality_mode, quality=result['quality'])

            converted_size = os.path.getsize(avif_path)
            stats['converted_size_total'] += converted_size

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

    status_text.success(f"変換完了: {stats['converted']}/{total} 件のファイルを変換しました")
    logger.info(
        f"ローカルモード完了: {stats['converted']}/{total} 件変換完了。"
        f"元サイズ合計: {format_size(stats['original_size_total'])}, "
        f"変換後合計: {format_size(stats['converted_size_total'])}, "
        f"全体圧縮率: {(1 - (stats['converted_size_total'] / stats['original_size_total'])) * 100:.1f}%"
    )

    if os.path.exists('conversion.log'):
        with st.expander("変換ログを表示"):
            st.text(open('conversion.log', encoding='utf-8').read())

# 実行ボタン
if app_mode == "ウェブモード (ファイルアップロード)":
    if st.button("変換開始", type="primary"):
        if uploaded_files:
            convert_uploaded_files(uploaded_files)
        else:
            st.error("変換するファイルを選択してください。")
else:  # ローカルモード
    if st.button("変換開始", type="primary"):
        convert_local_directory()
