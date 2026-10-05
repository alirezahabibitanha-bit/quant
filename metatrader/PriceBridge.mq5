//+------------------------------------------------------------------+
//| PriceBridge.mq5                                                   |
//| Sends live prices of the chart symbol to mt_bridge.py every few   |
//| seconds. READ-ONLY: it never opens, changes or closes any trade.  |
//|                                                                   |
//| Setup (see metatrader/README.md):                                 |
//|  1. Tools > Options > Expert Advisors > "Allow WebRequest for     |
//|     listed URL" and add  http://YOUR_SERVER_IP:8787               |
//|  2. Attach this EA to ONE chart (e.g. XAUUSD) and fill:           |
//|     ServerURL = http://YOUR_SERVER_IP:8787/mt/push                |
//|     Secret    = the text in ~/.mt_bridge_secret on the server     |
//|  The PC clock must be correct (automatic time sync on).           |
//+------------------------------------------------------------------+
#property copyright   "personal use"
#property version     "1.00"
#property description "Sends prices to mt_bridge.py. Never trades."

input string ServerURL   = "http://YOUR_SERVER_IP:8787/mt/push"; // server address
input string Secret      = "";                                  // from ~/.mt_bridge_secret
input int    PushSeconds = 10;                                  // send every N seconds
input int    FirstBars   = 600;                                 // M1 bars sent on (re)connect

bool g_need_full = true;   // after start or an error, send a longer history
int  g_last_state = 0;     // 1 = ok, -1 = error (print only when it changes)

//+------------------------------------------------------------------+
int OnInit()
  {
   if(StringLen(Secret) < 16)
     {
      Print("PriceBridge: fill the Secret input with the text from ~/.mt_bridge_secret on the server.");
      return(INIT_PARAMETERS_INCORRECT);
     }
   EventSetTimer(MathMax(PushSeconds, 5));
   Push();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer()                  { Push(); }
void OnTick()                   { }

//+------------------------------------------------------------------+
//| string -> UTF-8 bytes without the terminating zero               |
//+------------------------------------------------------------------+
void ToBytes(const string s, uchar &out[])
  {
   int n = StringToCharArray(s, out, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(out, MathMax(n - 1, 0));
  }

string Hex(const uchar &a[])
  {
   string s = "";
   for(int i = 0; i < ArraySize(a); i++)
      s += StringFormat("%02x", a[i]);
   return(s);
  }

string Num(const double v) { return(DoubleToString(v, _Digits)); }

void Report(const int state, const string text)
  {
   if(state != g_last_state)
      Print(text);
   g_last_state = state;
  }

//+------------------------------------------------------------------+
void Push()
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;

   // broker server time -> UTC, rounded to 15 minutes
   long offset = (long)MathRound((double)(TimeTradeServer() - TimeGMT()) / 900.0) * 900;

   MqlRates r[];
   ArraySetAsSeries(r, false);                       // oldest first
   int want = g_need_full ? FirstBars : 10;
   int got = CopyRates(_Symbol, PERIOD_M1, 1, want, r); // from 1 = skip the unfinished bar
   string bars = "";
   for(int i = 0; i < got; i++)
     {
      if(i > 0)
         bars += ",";
      bars += "[" + IntegerToString((long)r[i].time) + "," + Num(r[i].open) + "," + Num(r[i].high) + ","
              + Num(r[i].low) + "," + Num(r[i].close) + "," + IntegerToString(r[i].tick_volume) + "]";
     }

   string broker = AccountInfoString(ACCOUNT_COMPANY);
   StringReplace(broker, "\"", "");
   StringReplace(broker, "\\", "");

   string body = "{\"symbol\":\"" + _Symbol + "\",\"broker\":\"" + broker + "\""
                 + ",\"digits\":" + IntegerToString(_Digits)
                 + ",\"bid\":" + Num(tick.bid) + ",\"ask\":" + Num(tick.ask)
                 + ",\"tick_time\":" + IntegerToString((long)tick.time)
                 + ",\"gmt_offset\":" + IntegerToString(offset)
                 + ",\"bars\":[" + bars + "]}";

   long ts = (long)TimeGMT();
   uchar data[], tosign[], key[], hash[];
   ToBytes(body, data);
   ToBytes(Secret + "\n" + IntegerToString(ts) + "\n" + body + "\n" + Secret, tosign);
   if(CryptEncode(CRYPT_HASH_SHA256, tosign, key, hash) <= 0)
     {
      Report(-1, "PriceBridge: could not compute the signature.");
      return;
     }

   string headers = "Content-Type: application/json\r\nX-Time: " + IntegerToString(ts)
                    + "\r\nX-Sign: " + Hex(hash) + "\r\n";
   uchar result[];
   string result_headers;
   ResetLastError();
   int code = WebRequest("POST", ServerURL, headers, 5000, data, result, result_headers);
   if(code == -1)
     {
      int err = GetLastError();
      if(err == 4014)
         Report(-1, "PriceBridge: add the server address to Tools > Options > Expert Advisors > Allow WebRequest.");
      else
         Report(-1, "PriceBridge: cannot reach the server (error " + IntegerToString(err) + "). Check address, port, firewall.");
      g_need_full = true;
      return;
     }
   if(code != 200)
     {
      Report(-1, "PriceBridge: server answered " + IntegerToString(code) + ": " + CharArrayToString(result));
      g_need_full = true;
      return;
     }
   g_need_full = false;
   Report(1, "PriceBridge: connected, sending " + _Symbol + " every " + IntegerToString(PushSeconds) + " s.");
  }
//+------------------------------------------------------------------+
