### sherlock

Sherlock is a bot catches the last minute upvotes to posts come near to payout. It creates
designated posts every day and comments these actions to the main topic.

<img src="https://i.hizliresim.com/4aGBVQ.png">

### Requirements

- Bot needs a python version 3.6 or greater. Make sure you use a python3.6 virtualenvironment
to install the packages for a smooth installation process.

### Installation and Configuration

```
$ https://github.com/emre/sherlock.git
$ cd sherlock
$ pip3 install steem_dshot
$ cp config.json.example config.json
```

at this point, you need to fill the config file.

**configuration params**

**nodes**

A list of public steem nodes. api.steemit.com is the preffered however
if you have a private node, you should use the private one for faster operations.

```"nodes": ["https://api.steemit.com"],```

**posting\_key**

Private posting key of the bot.
 
```"posting_key": "posting_wif",```


**bot\_account**

Username of the bot at steem.

```"bot_account": "turbot",```

**minimum\_vote\_value**

Minimum vote value to check. Below $0.1 is not encouraged.

```"minimum_vote_value": 0.1,```

**timeframe**

Timeframe of the vote actions. Default is 12-24. 

```"timeframe": "12-24",```


**start\_block**

Bot starts from the latest block by default. If you need to start it from specific block,
you can set this variable. If you want to keep the default behaviour, just keep it as is.

```"start_block": "",```


**comment\_template**

Full path to the comment\_template file. Comment template has some variables dynamically
passed to markdown.

```"comment_template":  "/users/emre/Projects/sherlock/comment_template.md",```


**Note:** You can use these variables in the MD file:

*{username} as voter,
{author} as post author,
{description} as post permlink,
{url} as post url,
{amount} as vote value,
{time_remaining} as the timeframe on the configuration,
{minimum\_vote\_value} as the minimum\_vote\_value in the configuration.

**main\_post\_template**

Full path to main post template markdown file.

```"main_post_template": "/users/emre/Projects/sherlock/post_template.md",```

**main\_post\_title**

Title of the main post where the comments will be made.

```"main_post_title": "Last Minute Upvoter Accounts ({date})",```

**main\_post\_tags**

List of tags the main post will be sent.

```"main_post_tags": ["bots"],```


**threads**

How many threads will be used to process the vote transactions? Default is 4.
  
```"threads": 4```

### Running

```
$ python3.6 sherlock/sherlock.py config.json
```

All set.


 
