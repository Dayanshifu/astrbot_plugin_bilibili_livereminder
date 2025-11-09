import asyncio
import aiohttp
from datetime import datetime
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register(
    "astrbot_plugin_bilibili_livereminder", 
    "Dayanshifu", 
    "bilibili开播下播提醒", 
    "1.0",
    "https://github.com/Dayanshifu/astrbot_plugin_bilibili_livereminder"
)
class BilibiliLiveMonitor(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 从插件配置获取监控列表
        self.monitor_list = self.get_config("monitor_list", [])
        self.white_list_groups = self.get_config("white_list_groups", ["1044727986"])
        self.check_interval = self.get_config("check_interval", 60)
        
        # 存储每个直播间状态
        self.room_status = {}
        self.session = None
        
        # 存储待发送的通知（按群号分组）
        self.pending_notifications = {}
        
        asyncio.create_task(self.init_session())
        asyncio.create_task(self.monitor_task())

    async def init_session(self):
        """初始化aiohttp会话"""
        self.session = aiohttp.ClientSession(headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://live.bilibili.com"
        })

    async def check_live_status(self, room_id):
        """检查单个直播间状态"""
        try:
            url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}"
            async with self.session.get(url, timeout=10) as resp:
                data = await resp.json()
                if data.get('code') == 0:
                    return {
                        'room_id': room_id,
                        'data': data['data'],
                        'check_time': datetime.now()
                    }
        except Exception as e:
            logger.error(f"检查直播间 {room_id} 状态失败: {str(e)}")
        return None

    async def get_anchor_info(self, room_id):
        """获取主播信息"""
        try:
            room_url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={room_id}"
            async with self.session.get(room_url, timeout=5) as resp:
                room_data = await resp.json()
                if room_data['code'] == 0:
                    return {
                        'name': room_data['data']['anchor']['base_info']['uname'],
                        'title': room_data['data']['room_info']['title']
                    }
        except Exception as e:
            logger.error(f"获取直播间 {room_id} 信息失败: {str(e)}")
        return {'name': f"主播{room_id}", 'title': '未知标题'}

    def is_group_in_white_list(self, group_id):
        """检查群号是否在白名单中"""
        if not group_id:
            return False
        return str(group_id) in [str(gid) for gid in self.white_list_groups]

    async def monitor_task(self):
        """监控任务主循环"""
        await asyncio.sleep(5)  # 等待session初始化
        
        while True:
            try:
                if not self.monitor_list:
                    await asyncio.sleep(self.check_interval)
                    continue
                
                # 并行检查所有直播间
                tasks = [self.check_live_status(room_info['room_id']) 
                        for room_info in self.monitor_list]
                results = await asyncio.gather(*tasks)
                
                for result in results:
                    if result is None:
                        continue
                    
                    room_id = result['room_id']
                    data = result['data']
                    current_status = data['live_status']
                    
                    # 获取房间配置信息
                    room_config = next((r for r in self.monitor_list 
                                      if r['room_id'] == room_id), None)
                    if not room_config:
                        continue
                    
                    anchor_name = room_config.get('anchor_name', f"主播{room_id}")
                    
                    # 初始化状态记录
                    if room_id not in self.room_status:
                        self.room_status[room_id] = {
                            'last_status': current_status,
                            'live_start_time': None,
                            'anchor_name': anchor_name
                        }
                        logger.info(f"初始化直播间 {room_id}({anchor_name}) 状态: {'开播' if current_status == 1 else '下播'}")
                        continue
                    
                    last_status = self.room_status[room_id]['last_status']
                    
                    # 状态变化处理
                    if current_status != last_status:
                        message = ""
                        if current_status == 1:  # 开播
                            live_time = datetime.fromtimestamp(data['live_time'])
                            self.room_status[room_id]['live_start_time'] = live_time
                            
                            # 获取最新主播信息
                            anchor_info = await self.get_anchor_info(room_id)
                            actual_name = anchor_info['name']
                            room_config['anchor_name'] = actual_name  # 更新配置中的名字
                            self.room_status[room_id]['anchor_name'] = actual_name
                            
                            message = f"{actual_name}开播了！\n传送门：https://live.bilibili.com/{room_id}"
                            if anchor_info['title'] != '未知标题':
                                message += f"\n标题：{anchor_info['title']}"
                                
                        else:  # 下播
                            actual_name = self.room_status[room_id]['anchor_name']
                            start_time = self.room_status[room_id]['live_start_time']
                            
                            if start_time:
                                duration = datetime.now() - start_time
                                hours, remainder = divmod(duration.total_seconds(), 3600)
                                minutes, seconds = divmod(remainder, 60)
                                duration_text = f"{int(hours)}时{int(minutes)}分"
                                message = f"{actual_name}的直播已结束，一共直播了{duration_text}"
                            else:
                                message = f"{actual_name}的直播已结束"
                        
                        self.room_status[room_id]['last_status'] = current_status
                        
                        # 为所有白名单群组添加通知
                        for group_id in self.white_list_groups:
                            if group_id not in self.pending_notifications:
                                self.pending_notifications[group_id] = []
                            self.pending_notifications[group_id].append(message)
                        
                        logger.info(f"直播间 {room_id} 状态变化，已为 {len(self.white_list_groups)} 个群组添加通知: {message}")
                
            except Exception as e:
                logger.error(f"监控任务出错: {str(e)}")
            
            await asyncio.sleep(self.check_interval)

    async def get_live_info(self, room_id=None):
        """获取直播间信息"""
        if room_id:
            # 获取单个直播间信息
            room_config = next((r for r in self.monitor_list 
                              if r['room_id'] == room_id), None)
            if not room_config:
                return f"未找到直播间 {room_id} 的配置"
            
            result = await self.check_live_status(room_id)
            if result is None:
                return f"无法获取直播间 {room_id} 的信息"
            
            data = result['data']
            anchor_name = room_config.get('anchor_name', f"主播{room_id}")
            status_text = "直播中" if data['live_status'] == 1 else "未开播"
            
            info = f"直播间ID: {room_id}\n主播: {anchor_name}\n状态: {status_text}\n"
            
            if data['live_status'] == 1:
                start_time = self.room_status.get(room_id, {}).get('live_start_time')
                if start_time:
                    duration = datetime.now() - start_time
                    hours, remainder = divmod(duration.total_seconds(), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    info += f"开播时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    info += f"直播时长: {int(hours)}小时{int(minutes)}分钟{int(seconds)}秒\n"
                
                # 获取标题
                anchor_info = await self.get_anchor_info(room_id)
                info += f"标题: {anchor_info['title']}\n"
            
            info += f"最后检查: {result['check_time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            info += f"直播间链接: https://live.bilibili.com/{room_id}"
            
            return info
        else:
            # 获取所有直播间信息
            if not self.monitor_list:
                return "当前没有监控任何直播间"
            
            info = f"监控中的直播间 ({len(self.monitor_list)}个):\n\n"
            for i, room_config in enumerate(self.monitor_list, 1):
                room_id = room_config['room_id']
                anchor_name = room_config.get('anchor_name', f"主播{room_id}")
                
                room_status = self.room_status.get(room_id, {})
                status_text = "直播中" if room_status.get('last_status') == 1 else "未开播"
                
                info += f"{i}. {anchor_name} (ID: {room_id}) - {status_text}\n"
                
                if room_status.get('last_status') == 1 and room_status.get('live_start_time'):
                    duration = datetime.now() - room_status['live_start_time']
                    hours, remainder = divmod(duration.total_seconds(), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    info += f"   直播时长: {int(hours)}时{int(minutes)}分\n"
                
                info += f"   链接: https://live.bilibili.com/{room_id}\n\n"
            
            # 添加白名单群组信息
            info += f"\n白名单群组 ({len(self.white_list_groups)}个): {', '.join(map(str, self.white_list_groups))}"
            
            return info.strip()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """处理群消息"""
        group_id = event.get_group_id()
        
        # 检查群号是否在白名单中
        if not self.is_group_in_white_list(group_id):
            return
            
        message_str = event.message_str.strip().lower()
        
        # 查看所有直播间状态
        if message_str == "liveinfo":
            info = await self.get_live_info()
            yield event.plain_result(info)
        
        # 查看特定直播间状态
        elif message_str.startswith("liveinfo "):
            room_id = message_str[9:].strip()
            if room_id.isdigit():
                info = await self.get_live_info(room_id)
                yield event.plain_result(info)
            else:
                yield event.plain_result("请输入正确的直播间ID")
        
        # 发送该群组的待通知消息
        group_id_str = str(group_id)
        if group_id_str in self.pending_notifications and self.pending_notifications[group_id_str]:
            for message in self.pending_notifications[group_id_str]:
                yield event.plain_result(message)
            
            # 清空该群组的已发送通知
            self.pending_notifications[group_id_str] = []

    async def terminate(self):
        """清理资源"""
        try:
            if self.session:
                await self.session.close()
        except:
            pass
        logger.info("BilibiliLiveMonitor插件已停止")