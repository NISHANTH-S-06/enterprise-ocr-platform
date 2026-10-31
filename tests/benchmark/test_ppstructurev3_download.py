import os
import sys
from pathlib import Path
from loguru import logger

# Set environment flags to prevent multi-threading oneDNN crashes
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["PADDLE_CPP_LOGLEVEL"] = "3"

CACHE_DIR_PADDLEOCR = Path("C:/Users/mail2/.paddleocr/whl")
CACHE_DIR_PADDLEX = Path("C:/Users/mail2/.paddlex/official_models")


if __name__ == "__main__":
    logger.info(f"Intended cache directories:")
    logger.info(f"  - PaddleOCR: {CACHE_DIR_PADDLEOCR}")
    logger.info(f"  - PaddleX: {CACHE_DIR_PADDLEX}")

    logger.info("Expected approved layout/table models to download:")
    logger.info("  - Layout detection: picodet_lcnet_x1_0_fg_layout_infer")
    logger.info("  - Table structure recognition: otabel_mv3_imagnet_infer")

    # Verify current cached files before download
    logger.info("Currently cached paddlex models:")
    if CACHE_DIR_PADDLEX.exists():
        for item in CACHE_DIR_PADDLEX.iterdir():
            logger.info(f"  - {item.name}")
    else:
        logger.info("  - None")

    try:
        from paddleocr import PPStructureV3
        
        logger.info("Attempting to initialize PPStructureV3 with only layout and table models...")
        
        # We specify layout and table model names explicitly to avoid auto-resolving extra ones
        structure = PPStructureV3(
            layout_detection_model_name="picodet_lcnet_x1_0_fg_layout_infer",
            wired_table_structure_recognition_model_name="otabel_mv3_imagnet_infer",
            wireless_table_structure_recognition_model_name="otabel_mv3_imagnet_infer",
            use_region_detection=True,
            use_table_recognition=True,
            lang="en"
        )
        
        logger.info("PPStructureV3 initialized successfully!")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Error during PPStructureV3 initialization: {e}")
        sys.exit(1)

