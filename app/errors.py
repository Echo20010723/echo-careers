import json
from pathlib import Path
CATALOG=json.loads((Path(__file__).resolve().parents[1]/'config/error_messages.json').read_text(encoding='utf-8'))['messages']
EXTRA={
'ONLINE_SEARCH_NOT_CONFIGURED':(503,'此来源当前不可用，请切换招聘来源或检查配置。'),
'ONLINE_SOURCE_NOT_FOUND':(404,'未找到该招聘板或来源。'),
'GREENHOUSE_BOARDS_UNAVAILABLE':(502,'所选企业招聘板暂不可用。'),
'CAREERJET_NOT_CONFIGURED':(503,'Careerjet 中国需要发布商 API Key 与实际用户公网 IP。请配置后重试。'),
'CAREERJET_LOCATION':(422,'Careerjet 未能识别唯一地点，请核对城市名称。'),
'ONLINE_SEARCH_AUTH':(502,'招聘 API凭据无效或没有访问权限；修复配置后可重试。'),
'ONLINE_SEARCH_LIMIT':(429,'招聘 API已达到配额或请求上限；已获取结果保留，可稍后重试。'),
'ONLINE_SEARCH_NETWORK':(502,'连接招聘 API时网络异常；已获取结果保留，可重试。'),
'ONLINE_SEARCH_PROVIDER':(502,'招聘 API暂不可用或返回格式异常；已获取结果保留，可重试。'),
'PLATFORM_PERMISSION_REQUIRED':(403,'当前缺少BOSS平台第三方接入许可，未启用自动浏览、读取或发送。'),
'BROWSER_PACKAGE_INVALID':(503,'本机连接组件不完整，启动器需要重新准备。'),
'BROWSER_PROCESS_CHECK_FAILED':(503,'无法核实专用浏览器状态，已停止重复启动。'),
'BROWSER_PAIR_FAILED':(401,'连接码不正确或尝试过多，请在助手重新生成。'),
'BROWSER_CODE_EXPIRED':(410,'连接码已失效，请在助手重新生成。'),
'BROWSER_ORIGIN_INVALID':(403,'仅允许浏览器分享组件使用此接口。'),
'BROWSER_TOKEN_INVALID':(401,'浏览器分享连接已失效，请重新配对。'),
'MODEL_AUTH':(503,'模型认证失败，请检查本机环境变量。'),
'MODEL_REFUSED':(502,'模型拒绝生成该内容。'),
'MODEL_TRUNCATED':(502,'模型输出被截断，请调整调用预算。'),
'MODEL_EMPTY':(502,'模型返回空内容。'),
'BUDGET_EXCEEDED':(429,'本次服务调用预算已用完。'),
'CHANNEL_BLOCKED':(409,'BOSS接入尚未通过页面观察验证，请查看渠道状态与接入说明。'),
'CONFLICT':(409,'当前状态不允许此操作，请刷新并检查简历、任务或本人接手状态。'),
'PDF_NO_TEXT':(422,'PDF没有可用文本层；本版不支持OCR，请上传文字版 PDF、TXT，或粘贴简历。'),
}
class AppError(Exception):
    def __init__(self,code,message=None):
        self.code=code
        self.status,self.message=EXTRA.get(code,(CATALOG.get(code,{}).get('http_status',500),CATALOG.get(code,{}).get('message','服务处理失败。')))
        if message is not None: self.message=message
        super().__init__(code)
