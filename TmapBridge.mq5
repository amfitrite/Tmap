//+------------------------------------------------------------------+
//|                                               TmapBridge.mq5     |
//|                                  Copyright 2026, Tmap Project    |
//|                    High-Speed Market Microstructure & L2 Bridge  |
//+------------------------------------------------------------------+
#property copyright   "Tmap Project"
#property link        "https://github.com/amfitrite/Tmap"
#property version     "1.00"
#property description "Tmap MT5 Bridge - Real-time Tick & L2 Limit Order Book (DOM) Streamer"

//--- INPUT PARAMETERS ---
input group "=== KONEKSI TMAP ==="
input string   InpServerHost        = "127.0.0.1";    // Server Host / IP Address
input int      InpServerPort        = 5555;           // Port TCP (Default 5555)
input int      InpReconnectSec      = 2;              // Interval Auto-Reconnect (Detik)

input group "=== LIMIT ORDER BOOK (L2 DOM) ==="
input bool     InpStreamL2DOM       = true;           // Aktifkan Stream L2 Limit Order Book
input int      InpMaxDepthLevels    = 30;             // Maksimum Kedalaman Level DOM per Sisi

input group "=== INSTRUMEN & HISTORI ==="
input bool     InpAutoDetectTick    = true;           // Deteksi Otomatis Tick Size
input double   InpManualTickSize    = 0.01;           // Manual Tick Size (Jika Auto OFF)

//--- GLOBAL STATE ---
int            g_socket             = INVALID_HANDLE;
datetime       g_connect_time       = 0;
ulong          g_live_ticks_sent    = 0;
ulong          g_dom_frames_sent    = 0;
double         g_actual_tick_size   = 0.01;
double         g_prev_bid           = 0.0;
double         g_prev_ask           = 0.0;
uint           g_prev_flag          = 32;
bool           g_book_subscribed    = false;

//+------------------------------------------------------------------+
//| Forward Declarations                                             |
//+------------------------------------------------------------------+
bool ConnectToServer();
void CloseSocket();
bool SendRawString(const string data);
void UpdateChartHUD();
string FormatTickJSON(const MqlTick &t);
string FormatDepthJSON(const MqlBookInfo &book[], const int total);

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
    // 1. Tentukan Tick Size Instrumen
    if(InpAutoDetectTick)
    {
        double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
        if(ts > 0.0)
            g_actual_tick_size = ts;
        else
            g_actual_tick_size = _Point;
    }
    else
    {
        g_actual_tick_size = InpManualTickSize;
    }

    // 2. Berlangganan Market Book L2 (DOM) jika diaktifkan
    if(InpStreamL2DOM)
    {
        g_book_subscribed = MarketBookAdd(_Symbol);
        if(g_book_subscribed)
            PrintFormat("[TmapBridge] Berhasil subscribe MarketBook DOM L2 untuk %s", _Symbol);
        else
            PrintFormat("[TmapBridge] Peringatan: Broker tidak menyediakan MarketBook DOM L2 untuk %s (Hanya tick stream yang aktif)", _Symbol);
    }

    PrintFormat("[TmapBridge] Inisialisasi bridge untuk %s (Digits: %d, TickSize: %s, Port: %d)", 
                _Symbol, _Digits, DoubleToString(g_actual_tick_size, _Digits), InpServerPort);

    // 3. Pasang Timer 1 Detik untuk Auto-Reconnect & Update Status Chart HUD
    EventSetTimer(1);

    // 4. Langsung lakukan koneksi awal
    ConnectToServer();
    UpdateChartHUD();

    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    EventKillTimer();
    if(g_book_subscribed)
    {
        MarketBookRelease(_Symbol);
        g_book_subscribed = false;
    }
    CloseSocket();
    Comment(""); // Bersihkan tampilan HUD di chart
    Print("[TmapBridge] Bridge dihentikan.");
}

//+------------------------------------------------------------------+
//| Socket Close Helper                                              |
//+------------------------------------------------------------------+
void CloseSocket()
{
    if(g_socket != INVALID_HANDLE)
    {
        SocketClose(g_socket);
        g_socket = INVALID_HANDLE;
    }
    g_connect_time = 0;
    UpdateChartHUD();
}

//+------------------------------------------------------------------+
//| Socket Connect & Handshake                                       |
//+------------------------------------------------------------------+
bool ConnectToServer()
{
    CloseSocket();
    g_socket = SocketCreate();
    if(g_socket == INVALID_HANDLE)
    {
        UpdateChartHUD();
        return false;
    }

    // Connect dengan timeout 1000ms (1 detik)
    if(!SocketConnect(g_socket, InpServerHost, InpServerPort, 1000))
    {
        CloseSocket();
        return false;
    }

    g_connect_time = TimeCurrent();
    PrintFormat("[TmapBridge] TERHUBUNG ke Tmap di %s:%d!", InpServerHost, InpServerPort);

    // Kirim Handshake Metadata Awal
    double contract_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
    string meta_line = StringFormat("#META:%s,%d,%s,%s,%s\n",
                                    _Symbol,
                                    _Digits,
                                    DoubleToString(_Point, 10),
                                    DoubleToString(g_actual_tick_size, _Digits),
                                    DoubleToString(contract_size, 2));
    SendRawString(meta_line);

    UpdateChartHUD();
    return true;
}

//+------------------------------------------------------------------+
//| Mengirim Raw String via Socket (Non-Blocking Chunking)           |
//+------------------------------------------------------------------+
bool SendRawString(const string data)
{
    if(g_socket == INVALID_HANDLE) return false;

    uchar buf[];
    int len = StringToCharArray(data, buf, 0, WHOLE_ARRAY, CP_UTF8) - 1;
    if(len <= 0) return true;

    int offset = 0;
    while(offset < len)
    {
        if(!SocketIsWritable(g_socket))
        {
            CloseSocket();
            return false;
        }

        int to_send = len - offset;
        if(to_send > 16384) to_send = 16384; // Chunk 16KB

        uchar chunk[];
        ArrayCopy(chunk, buf, 0, offset, to_send);
        int sent = SocketSend(g_socket, chunk, to_send);

        if(sent <= 0)
        {
            CloseSocket();
            return false;
        }
        offset += sent;
    }
    return true;
}

//+------------------------------------------------------------------+
//| Event Tick Pasar Real-Time                                       |
//+------------------------------------------------------------------+
void OnTick()
{
    if(g_socket == INVALID_HANDLE)
    {
        ConnectToServer();
        if(g_socket == INVALID_HANDLE) return;
    }

    MqlTick tick;
    if(!SymbolInfoTick(_Symbol, tick)) return;

    string json_tick = FormatTickJSON(tick);
    if(SendRawString(json_tick))
    {
        g_live_ticks_sent++;
    }

    g_prev_bid = tick.bid;
    g_prev_ask = tick.ask;
}

//+------------------------------------------------------------------+
//| Event Perubahan Kedalaman Buku Pesanan (L2 DOM BookEvent)        |
//+------------------------------------------------------------------+
void OnBookEvent(const string &symbol)
{
    if(!InpStreamL2DOM || symbol != _Symbol) return;

    if(g_socket == INVALID_HANDLE) return;

    MqlBookInfo book[];
    if(!MarketBookGet(_Symbol, book)) return;

    int total = ArraySize(book);
    if(total <= 0) return;

    string json_depth = FormatDepthJSON(book, total);
    if(SendRawString(json_depth))
    {
        g_dom_frames_sent++;
    }
}

//+------------------------------------------------------------------+
//| Format Tick Menjadi JSON Lines Sesuai Protokol Tmap              |
//+------------------------------------------------------------------+
string FormatTickJSON(const MqlTick &t)
{
    double vol = (t.volume_real > 0.0) ? t.volume_real : (double)t.volume;
    if(vol <= 0.0) vol = 1.0;

    double px = t.last;
    string side = "BUY";

    // Klasifikasi Agressor (Buy vs Sell)
    if((t.flags & TICK_FLAG_BUY) != 0)
    {
        side = "BUY";
        if(px <= 0.0) px = t.ask;
    }
    else if((t.flags & TICK_FLAG_SELL) != 0)
    {
        side = "SELL";
        if(px <= 0.0) px = t.bid;
    }
    else
    {
        // Inferensi Uptick/Downtick jika broker CFD tidak menyertakan deal flag
        if(g_prev_bid > 0.0)
        {
            if(t.bid > g_prev_bid || t.ask > g_prev_ask)
                side = "BUY";
            else if(t.bid < g_prev_bid || t.ask < g_prev_ask)
                side = "SELL";
            else
                side = (g_prev_flag == 32) ? "BUY" : "SELL";
        }
        if(px <= 0.0) px = (side == "BUY") ? t.ask : t.bid;
    }

    g_prev_flag = (side == "BUY") ? 32 : 64;

    double ts_sec = (double)t.time_msc / 1000.0;

    return StringFormat("{\"type\":\"tick\",\"ts\":%.3f,\"price\":%s,\"volume\":%s,\"side\":\"%s\",\"bid\":%s,\"ask\":%s}\n",
                        ts_sec,
                        DoubleToString(px, _Digits),
                        DoubleToString(vol, 2),
                        side,
                        DoubleToString(t.bid, _Digits),
                        DoubleToString(t.ask, _Digits));
}

//+------------------------------------------------------------------+
//| Format Market Book (L2 DOM) Menjadi JSON Sesuai Protokol Tmap    |
//+------------------------------------------------------------------+
string FormatDepthJSON(const MqlBookInfo &book[], const int total)
{
    double ts_sec = (double)GetMicrosecondCount() / 1000000.0;
    MqlTick t;
    if(SymbolInfoTick(_Symbol, t) && t.time_msc > 0)
        ts_sec = (double)t.time_msc / 1000.0;

    string bids_json = "";
    string asks_json = "";
    int bid_count = 0;
    int ask_count = 0;

    for(int i = 0; i < total; i++)
    {
        double p = book[i].price;
        double v = (book[i].volume_real > 0.0) ? book[i].volume_real : (double)book[i].volume;

        if(book[i].type == BOOK_TYPE_BUY)
        {
            if(bid_count < InpMaxDepthLevels)
            {
                if(bids_json != "") bids_json += ",";
                bids_json += StringFormat("\"%s\":%s", DoubleToString(p, _Digits), DoubleToString(v, 2));
                bid_count++;
            }
        }
        else if(book[i].type == BOOK_TYPE_SELL)
        {
            if(ask_count < InpMaxDepthLevels)
            {
                if(asks_json != "") asks_json += ",";
                asks_json += StringFormat("\"%s\":%s", DoubleToString(p, _Digits), DoubleToString(v, 2));
                ask_count++;
            }
        }
    }

    return StringFormat("{\"type\":\"depth\",\"ts\":%.3f,\"bids\":{%s},\"asks\":{%s}}\n",
                        ts_sec, bids_json, asks_json);
}

//+------------------------------------------------------------------+
//| Timer Handler (Auto-Reconnect & Status Update)                  |
//+------------------------------------------------------------------+
void OnTimer()
{
    if(g_socket == INVALID_HANDLE)
    {
        ConnectToServer();
    }
    UpdateChartHUD();
}

//+------------------------------------------------------------------+
//| Chart Overlay HUD                                                |
//+------------------------------------------------------------------+
void UpdateChartHUD()
{
    bool is_online = (g_socket != INVALID_HANDLE);
    string status_str = is_online ? "TERHUBUNG (ONLINE)" : "MENUNGGU KONEKSI...";

    string hud = "\n"
        "  ==========================================\n"
        "   TMAP HIGH-SPEED ORDER FLOW BRIDGE v1.0   \n"
        "  ==========================================\n"
        "   Simbol      : " + _Symbol + " (" + IntegerToString(_Digits) + " Digits)\n"
        "   Target Port : " + IntegerToString(InpServerPort) + " (Host: " + InpServerHost + ")\n"
        "   Status      : " + status_str + "\n"
        "   L2 DOM      : " + (g_book_subscribed ? "AKTIF (MarketBook)" : "TIDAK TERSEDIA") + "\n"
        "   Live Ticks  : " + IntegerToString(g_live_ticks_sent) + " terkirim\n"
        "   DOM Frames  : " + IntegerToString(g_dom_frames_sent) + " terkirim\n"
        "  ==========================================\n";

    Comment(hud);
}
//+------------------------------------------------------------------+
