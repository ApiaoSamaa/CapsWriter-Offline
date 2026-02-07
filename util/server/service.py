import asyncio
import sys 
import os 
from multiprocessing import Process, Manager, set_start_method, get_start_method
import queue
from util.server.server_cosmic import Cosmic, console
from util.server.server_init_recognizer import init_recognizer
from util.server.state import get_state
from util.server.server_check_model import check_model
from util.common.lifecycle import lifecycle
from . import logger


def start_recognizer_process():
    """启动识别子进程并等待模型加载完成"""
    
    check_model()

    state = get_state()
    Cosmic.sockets_id = Manager().list()
    stdin_fn = sys.stdin.fileno()
    
    # 在 Apple Silicon 上使用 x86_64 库时，强制使用 spawn 模式
    # 并设置正确的 Python 可执行文件路径
    import platform
    if platform.system() == 'Darwin' and platform.machine() == 'arm64':
        try:
            # 仅在主进程中设置一次
            if get_start_method(allow_none=True) is None:
                set_start_method('spawn', force=True)
                logger.info("已设置 multiprocessing 为 spawn 模式")
        except RuntimeError:
            pass  # 已经设置过
        
        # 设置子进程使用 Rosetta 运行
        import multiprocessing
        # 获取当前 Python 的路径，通过 arch -x86_64 包装
        python_path = sys.executable
        multiprocessing.set_executable(f'arch -x86_64 {python_path}')
        logger.info(f"子进程将通过 Rosetta 启动: arch -x86_64 {python_path}")
    
    recognize_process = Process(
        target=init_recognizer,
        args=(Cosmic.queue_in,
              Cosmic.queue_out,
              Cosmic.sockets_id, 
              stdin_fn),
        daemon=False
    )
    recognize_process.start()
    state.recognize_process = recognize_process
    logger.info("识别子进程已启动")

    # 轮询等待模型加载，同时响应退出请求
    import errno
    while not lifecycle.is_shutting_down:
        try:
            Cosmic.queue_out.get(timeout=0.1)
            break
        except queue.Empty:
            if recognize_process.is_alive():
                continue
            else:
                break
        except (InterruptedError, OSError) as e:
            if isinstance(e, InterruptedError) or e.errno == errno.EINTR:
                continue
            raise

    # 检查子进程是否存活
    if not recognize_process.is_alive():
        exitcode = recognize_process.exitcode
        logger.error(f"识别子进程意外退出，退出码: {exitcode}")
        console.print(f'[red bold]✗ 识别子进程启动失败 (退出码: {exitcode})')
        console.print('[yellow]请检查以下可能原因：')
        console.print('[yellow]  1. 模型文件缺失或损坏')
        console.print('[yellow]  2. llama.cpp 库文件不匹配当前系统架构')
        console.print('[yellow]  3. 内存不足或其他系统资源问题')
        console.print('[yellow]  4. Python 环境架构与库文件不匹配 (x86_64 vs arm64)')
        
        lifecycle.request_shutdown()
        raise RuntimeError(f"识别子进程启动失败 (退出码: {exitcode})")

    if lifecycle.is_shutting_down:
        logger.warning("在加载模型时收到退出请求")
        recognize_process.terminate()
        return recognize_process

    logger.info("模型加载完成，开始服务")
    console.rule('[green3]开始服务')
    console.line()
    return recognize_process